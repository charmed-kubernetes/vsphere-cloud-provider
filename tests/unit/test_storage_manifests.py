# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


from unittest import mock

from storage_manifests import CreateStorageClass


def test_parse_parameter_config():
    mock_manifests = mock.MagicMock()
    mock_manifests.config = {"storage-class-parameters": "key=val,something=test with spaces"}
    sc = CreateStorageClass(mock_manifests, "default")
    assert sc._params == {"key": "val", "something": "test with spaces"}


def test_parse_parameter_invalid(caplog):
    mock_manifests = mock.MagicMock()
    mock_manifests.config = {"storage-class-parameters": "key=val,something"}
    sc = CreateStorageClass(mock_manifests, "default")
    assert sc._params == {"key": "val"}
    error_logs = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert len(error_logs) == 1, "Expect one error"
    assert error_logs[0].message == "Storage class parameter missing '=' separator in 'something'"
