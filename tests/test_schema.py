import pyarrow as pa

from core import schema_parser


def test_json_type_to_arrow_basic():

    assert schema_parser.json_type_to_arrow("string") == pa.string()
    assert schema_parser.json_type_to_arrow("int") == pa.int64()
    assert schema_parser.json_type_to_arrow("bool") == pa.bool_()


def test_json_type_to_arrow_array():

    res = schema_parser.json_type_to_arrow("array")
    assert res == pa.list_(pa.string())
