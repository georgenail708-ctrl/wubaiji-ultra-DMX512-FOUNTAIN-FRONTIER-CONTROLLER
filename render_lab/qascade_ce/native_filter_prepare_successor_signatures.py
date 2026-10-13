"""Explicit reviewed P01 successor roles; baseline tables remain untouched."""
from .native_filter_prepare_signatures import NEW_EXPECTED, NEW_FUNCTION_NAMES

SUCCESSOR_EXPECTED = {
    "native_filter_prepare_guard:Guard.current": "5f420603945101a579d613fd73ef82f0e370fb333b177b80aaabe9092db084d4",
    "native_filter_prepare_guard:function_current_successor": "57b77b0a173202f79d18795fd6106f24302e4279f145c21be4e20bf155dfc534",
    "native_filter_prepare_guard:_plain_metadata_current": "b97fb1edc3f70c4168281540b9aa7151532c8cef0505532a796d8c145a27a6a8",
    "native_filter_prepare_guard:Guard.current_original": "50d3536c4bcecec54db9ad20af0c761975633ce1d96df1ab4386322bb98bd8ad",
    "native_filter_prepare_guard:_new_current_successor": "3691005f163b31b709565ada6e605f75bcf1978861fea412f716bfdcd801a7eb"
}

SUCCESSOR_FUNCTION_NAMES = (
    'function_current_successor',
    '_plain_metadata_current',
    'Guard.current_original',
    '_new_current_successor',
)

ADMITTED_EXPECTED = NEW_EXPECTED | SUCCESSOR_EXPECTED
ADMITTED_FUNCTION_NAMES = dict(NEW_FUNCTION_NAMES)
ADMITTED_FUNCTION_NAMES['native_filter_prepare_guard'] += SUCCESSOR_FUNCTION_NAMES
