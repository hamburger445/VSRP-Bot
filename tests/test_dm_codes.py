import os

os.environ.setdefault("CAD_CODE_PEPPER", "test-pepper")

from cad.security import generate_dm_code, hash_dm_code


def test_hash_is_not_plaintext():
    code = "123456"
    digest = hash_dm_code(code)
    assert digest != code
    assert hash_dm_code(code) == digest
    assert hash_dm_code("000000") != digest


def test_generate_six_digits():
    code = generate_dm_code()
    assert len(code) == 6
    assert code.isdigit()
