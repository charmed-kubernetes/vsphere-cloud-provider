# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock
from pathlib import Path

import pytest
import yaml
from ops.charm import RelationBrokenEvent
from ops.testing import Harness

from charm import VsphereCloudProviderCharm


@pytest.fixture
def harness():
    harness = Harness(VsphereCloudProviderCharm)
    try:
        yield harness
    finally:
        harness.cleanup()


@pytest.fixture(autouse=True)
def mock_ca_cert(tmpdir):
    ca_cert = Path(tmpdir) / "ca.crt"
    ca_cert.write_bytes(b"abcd")

    with mock.patch("charm.VsphereCloudProviderCharm.CA_CERT_PATH", ca_cert):
        yield ca_cert


@pytest.fixture()
def relation_data():
    yield yaml.safe_load(Path("tests/data/kube_control_data.yaml").read_text())


@pytest.mark.parametrize(
    "event_type", [None, RelationBrokenEvent], ids=["unrelated", "dropped relation"]
)
def test_is_ready_no_relation(harness: Harness, event_type):
    harness.begin()

    with mock.patch(
        "requires_kube_control.KubeControlRequires.relation", new_callable=mock.PropertyMock
    ) as mock_prop:
        relation = mock_prop.return_value
        relation.__bool__.return_value = event_type is not None
        relation.units = []
        event = mock.MagicMock(spec=event_type)
        event.relation = relation
        assert harness.charm.kube_control.is_ready is False
        assert "Missing" in harness.charm.kube_control.evaluate_relation(event)


def test_is_ready_invalid_data(harness: Harness, relation_data):
    harness.begin()
    relation_data["domain"] = 123
    with mock.patch(
        "requires_kube_control.KubeControlRequires.relation", new_callable=mock.PropertyMock
    ) as mock_prop:
        relation = mock_prop.return_value
        relation.units = ["remote/0"]
        relation.data = {"remote/0": relation_data}
        assert harness.charm.kube_control.is_ready is False


def test_is_ready_success(harness: Harness, relation_data):
    harness.begin()
    with mock.patch(
        "requires_kube_control.KubeControlRequires.relation", new_callable=mock.PropertyMock
    ) as mock_prop:
        relation = mock_prop.return_value
        relation.units = ["remote/0"]
        relation.data = {"remote/0": relation_data}
        assert harness.charm.kube_control.is_ready is True


def test_create_kubeconfig(harness, relation_data, mock_ca_cert, tmpdir):
    harness.begin()
    with mock.patch(
        "requires_kube_control.KubeControlRequires.relation", new_callable=mock.PropertyMock
    ) as mock_prop:
        relation = mock_prop.return_value
        relation.units = ["remote/0"]
        relation.data = {"remote/0": relation_data}

        kube_config = Path(tmpdir) / "kube_config"

        # First run creates a new file
        assert not kube_config.exists()
        harness.charm.kube_control.create_kubeconfig(
            mock_ca_cert, kube_config, "ubuntu", harness.charm.unit.name
        )
        config = yaml.safe_load(kube_config.read_text())
        assert config["kind"] == "Config"

        # Second call alters existing file
        kube_config.write_text("")
        harness.charm.kube_control.create_kubeconfig(
            mock_ca_cert, kube_config, "ubuntu", harness.charm.unit.name
        )
        config = yaml.safe_load(kube_config.read_text())
        assert config["kind"] == "Config"
