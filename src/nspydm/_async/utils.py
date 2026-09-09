"""异步工具函数。

从 dmAsync 移植的 _ContextManager、get_running_loop 等基础组件。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Coroutine, Generator
from functools import partial
from types import TracebackType
from typing import Any, Callable, Generic, TypeVar

__all__ = (
    "get_running_loop",
    "create_completed_future",
    "_to_thread",
    "_ContextManager",
)


def get_running_loop() -> asyncio.AbstractEventLoop:
    if sys.version_info >= (3, 7, 0):  # noqa: UP036
        return asyncio.get_running_loop()
    loop = asyncio.get_event_loop()
    if not loop.is_running():
        raise RuntimeError("no running event loop")
    return loop


def create_completed_future(
    loop: asyncio.AbstractEventLoop,
) -> asyncio.Future[Any]:
    future = loop.create_future()
    future.set_result(None)
    return future


_TObj = TypeVar("_TObj")


async def _to_thread(func: Callable[..., _TObj], *args: Any, **kwargs: Any) -> _TObj:
    """兼容 Python 3.9+ 的 asyncio.to_thread 封装。

    Python 3.9+ 使用 ``asyncio.to_thread``，
    更早版本回退到 ``loop.run_in_executor(None, ...)``。
    """
    if sys.version_info >= (3, 9, 0):  # noqa: UP036
        if kwargs:
            return await asyncio.to_thread(partial(func, *args, **kwargs))
        return await asyncio.to_thread(func, *args)
    loop = get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))
    return await loop.run_in_executor(None, func, *args)


_Release = Callable[[_TObj], Awaitable[None]]


class _ContextManager(Coroutine[Any, Any, _TObj], Generic[_TObj]):
    """可同时 await 和 async with 的上下文管理器。

    包装一个资源获取协程，配合 release 回调实现自动释放。
    从 dmAsync 移植。

    用法::

        async def acquire():
            return await create_resource()

        async def release(resource):
            await resource.close()

        # 直接 await 获取资源
        res = await _ContextManager(acquire(), release)

        # 或使用 async with 自动释放
        async with _ContextManager(acquire(), release) as res:
            ...
    """

    __slots__ = ("_coro", "_obj", "_release", "_release_on_exception")

    def __init__(
        self,
        coro: Coroutine[Any, Any, _TObj],
        release: _Release[_TObj],
        release_on_exception: _Release[_TObj] | None = None,
    ) -> None:
        self._coro = coro
        self._obj: _TObj | None = None
        self._release = release
        self._release_on_exception = (
            release if release_on_exception is None else release_on_exception
        )

    def send(self, value: Any) -> Any:
        return self._coro.send(value)

    def throw(  # type: ignore[override]
        self,
        __type: type[BaseException],
        __val: object = None,
        __tb: TracebackType | None = None,
    ) -> Any:
        if __val is None:
            return self._coro.throw(__type)
        if __tb is None:
            return self._coro.throw(__type, __val)
        return self._coro.throw(__type, __val, __tb)

    def close(self) -> None:
        self._coro.close()

    def __await__(self) -> Generator[Any, Any, _TObj]:
        return self._coro.__await__()

    async def __aenter__(self) -> _TObj:
        self._obj = await self._coro
        return self._obj

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._obj is None:
            return
        try:
            if exc_type is not None:
                await self._release_on_exception(self._obj)
            else:
                await self._release(self._obj)
        finally:
            self._obj = None
