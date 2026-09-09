"""基础冒烟测试 — 不依赖真实数据库。"""

import unittest


class TestParamstyle(unittest.TestCase):
    """参数风格适配测试。"""

    def test_percent_s_to_qmark(self):
        from nspydm._backends._native import _adapt_paramstyle

        self.assertEqual(
            _adapt_paramstyle("select * from t where a=%s and b=%s"),
            "select * from t where a=? and b=?",
        )

    def test_percent_s_inside_quotes_preserved(self):
        from nspydm._backends._native import _adapt_paramstyle

        self.assertEqual(
            _adapt_paramstyle("select '%s' as x, col from t where a=%s"),
            "select '%s' as x, col from t where a=?",
        )

    def test_qmark_unchanged(self):
        from nspydm._backends._native import _adapt_paramstyle

        self.assertEqual(
            _adapt_paramstyle("select * from t where a=? and b=?"),
            "select * from t where a=? and b=?",
        )

    def test_no_placeholder_unchanged(self):
        from nspydm._backends._native import _adapt_paramstyle

        self.assertEqual(
            _adapt_paramstyle("select 1"),
            "select 1",
        )

    def test_percent_s_in_double_quotes_preserved(self):
        from nspydm._backends._native import _adapt_paramstyle

        self.assertEqual(
            _adapt_paramstyle('select "%s" as x from t where a=%s'),
            'select "%s" as x from t where a=?',
        )


class TestValueConversion(unittest.TestCase):
    """值类型转换测试。"""

    def test_none_value(self):
        from nspydm._backends._native import _convert_value
        self.assertIsNone(_convert_value(None, 12))

    def test_string_value(self):
        from nspydm._backends._native import _convert_value
        self.assertEqual(_convert_value("hello", 12), "hello")

    def test_clob_value(self):
        from nspydm._backends._native import _convert_value
        self.assertEqual(_convert_value("clob text", 2005), "clob text")

    def test_integer_value(self):
        from nspydm._backends._native import _convert_value
        self.assertEqual(_convert_value(42, 4), 42)

    def test_float_value(self):
        from nspydm._backends._native import _convert_value
        self.assertEqual(_convert_value(3.14, 8), 3.14)

    def test_date_value(self):
        import datetime

        from nspydm._backends._native import _convert_value
        result = _convert_value("2026-06-13", 91)
        # DATE 类型(91)应返回 date 对象
        self.assertIsInstance(result, datetime.date)
        self.assertEqual(result, datetime.date(2026, 6, 13))

    def test_timestamp_value(self):
        import datetime

        from nspydm._backends._native import _convert_value
        result = _convert_value("2026-06-13 10:30:00", 93)
        # TIMESTAMP 类型(93)应返回 datetime 对象
        self.assertIsInstance(result, datetime.datetime)
        self.assertEqual(result, datetime.datetime(2026, 6, 13, 10, 30, 0))

    def test_blob_value(self):
        import base64

        from nspydm._backends._native import _convert_value
        raw = base64.b64encode(b"binary data").decode("ascii")
        result = _convert_value(raw, 2004)
        # BLOB 类型(2004)应返回 bytes
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, b"binary data")

    def test_decimal_value(self):
        import decimal

        from nspydm._backends._native import _convert_value
        result = _convert_value("123.45", 3)
        # DECIMAL 类型(3)应返回 Decimal
        self.assertIsInstance(result, decimal.Decimal)
        self.assertEqual(result, decimal.Decimal("123.45"))


class TestParamConversion(unittest.TestCase):
    """参数值转换测试。"""

    def test_none_param(self):
        from nspydm._backends._native import _param_to_json
        self.assertIsNone(_param_to_json(None))

    def test_bool_param(self):
        from nspydm._backends._native import _param_to_json
        self.assertTrue(_param_to_json(True))
        self.assertFalse(_param_to_json(False))

    def test_datetime_param(self):
        import datetime

        from nspydm._backends._native import _param_to_json
        result = _param_to_json(datetime.datetime(2026, 6, 13, 10, 30, 0))
        self.assertEqual(result, "2026-06-13 10:30:00")

    def test_bytes_param(self):
        import base64

        from nspydm._backends._native import _param_to_json
        result = _param_to_json(b"hello")
        self.assertEqual(result, base64.b64encode(b"hello").decode("ascii"))

    def test_decimal_param(self):
        import decimal

        from nspydm._backends._native import _param_to_json
        result = _param_to_json(decimal.Decimal("99.99"))
        self.assertEqual(result, "99.99")


class TestDbapiImports(unittest.TestCase):
    """DB-API 2.0 接口导入测试。"""

    def test_import_nspydm(self):
        import nspydm
        self.assertEqual(nspydm.apilevel, "2.0")

    def test_connect_callable(self):
        import nspydm
        self.assertTrue(callable(nspydm.connect))

    def test_exception_hierarchy(self):
        import nspydm
        self.assertTrue(issubclass(nspydm.OperationalError, nspydm.DatabaseError))
        self.assertTrue(issubclass(nspydm.DatabaseError, nspydm.Error))
        self.assertTrue(issubclass(nspydm.InterfaceError, nspydm.Error))

    def test_type_objects(self):
        import nspydm
        self.assertEqual(nspydm.STRING, "STRING")
        self.assertEqual(nspydm.BINARY, "BINARY")
        self.assertEqual(nspydm.NUMBER, "NUMBER")
        self.assertEqual(nspydm.DATETIME, "DATETIME")
        self.assertEqual(nspydm.ROWID, "ROWID")

    def test_pool_imports(self):
        from nspydm import ConnectionPool, create_pool
        self.assertTrue(callable(create_pool))
        self.assertTrue(callable(ConnectionPool))


class TestArchDetection(unittest.TestCase):
    """架构检测测试。"""

    def test_arch_suffix_not_empty(self):
        import platform

        from nspydm.native.bridge import _get_arch_suffix
        suffix = _get_arch_suffix()
        # 当前平台应有匹配的后缀
        system = platform.system().lower()
        if system in ("darwin", "linux", "windows"):
            self.assertTrue(len(suffix) > 0, f"arch suffix should not be empty on {system}")
        # 后缀格式应为 platform-arch
        self.assertRegex(suffix, r"^\w+-\w+$")


if __name__ == "__main__":
    unittest.main()
