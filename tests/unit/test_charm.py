# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import base64
import unittest.mock as mock
from pathlib import Path

import ops.testing
import pytest
import yaml
from ops.model import BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness

from charm import VsphereCloudProviderCharm

ops.testing.SIMULATE_CAN_CONNECT = True


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
    with mock.patch.object(VsphereCloudProviderCharm, "CA_CERT_PATH", ca_cert):
        yield ca_cert


@pytest.fixture()
def integrator():
    with mock.patch("charm.VsphereIntegrationRequires") as mocked:
        vsphereintegrator = mocked.return_value
        vsphereintegrator.vsphere_ip = "1.2.3.4"
        vsphereintegrator.user = "alice"
        vsphereintegrator.password = "bob"
        vsphereintegrator.datacenter = "Elbonia"
        vsphereintegrator.evaluate_relation.return_value = None
        yield vsphereintegrator


@pytest.fixture()
def certificates():
    with mock.patch("charm.CertificatesRequires") as mocked:
        certificates = mocked.return_value
        certificates.ca = "abcd"
        certificates.evaluate_relation.return_value = None
        yield certificates


@pytest.fixture()
def kube_control():
    with mock.patch("charm.KubeControlRequirer") as mocked:
        kube_control = mocked.return_value
        kube_control.evaluate_relation.return_value = None
        kube_control.get_registry_location.return_value = "ghcr.io/canonical/cdk"
        kube_control.get_controller_taints.return_value = []
        kube_control.get_controller_labels.return_value = []
        kube_control.relation.app.name = "kubernetes-control-plane"
        kube_control.relation.units = [f"kubernetes-control-plane/{_}" for _ in range(2)]
        yield kube_control


def test_waits_for_integrator(harness):
    harness.begin_with_initial_hooks()
    charm = harness.charm
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required vsphere-integration relation"


@pytest.mark.usefixtures("integrator")
def test_waits_for_certificates(harness):
    harness.begin_with_initial_hooks()
    charm = harness.charm
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required certificates"

    # Test adding the certificates relation
    rel_cls = type(charm.certificates)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._raw_data = property(rel_cls._raw_data.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("certificates", "easyrsa")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for certificates"
    harness.add_relation_unit(rel_id, "easyrsa/0")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for certificates"
    harness.update_relation_data(
        rel_id,
        "easyrsa/0",
        yaml.safe_load(Path("tests/data/certificates_data.yaml").read_text()),
    )
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required kube-control relation"


@mock.patch("ops.interface_kube_control.KubeControlRequirer.create_kubeconfig")
@pytest.mark.usefixtures("integrator", "certificates")
def test_waits_for_kube_control(mock_create_kubeconfig, harness):
    harness.begin_with_initial_hooks()
    charm = harness.charm
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required kube-control relation"

    # Add the kube-control relation
    rel_cls = type(charm.kube_control)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("kube-control", "kubernetes-control-plane")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for kube-control relation"

    harness.add_relation_unit(rel_id, "kubernetes-control-plane/0")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for kube-control relation"
    mock_create_kubeconfig.assert_not_called()

    harness.update_relation_data(
        rel_id,
        "kubernetes-control-plane/0",
        yaml.safe_load(Path("tests/data/kube_control_data.yaml").read_text()),
    )
    mock_create_kubeconfig.assert_has_calls(
        [
            mock.call(charm.CA_CERT_PATH, "/root/.kube/config", "root", charm.unit.name),
            mock.call(charm.CA_CERT_PATH, "/home/ubuntu/.kube/config", "ubuntu", charm.unit.name),
        ]
    )
    assert isinstance(charm.unit.status, MaintenanceStatus)
    assert charm.unit.status.message == "Deploying vSphere Cloud Provider"


@pytest.mark.usefixtures("integrator", "certificates", "kube_control")
def test_waits_for_config(harness: Harness, lk_client, caplog):
    harness.begin_with_initial_hooks()
    with mock.patch.object(lk_client, "list") as mock_list:
        mock_list.return_value = [mock.Mock(**{"metadata.annotations": {}})]
        caplog.clear()
        harness.update_config(
            {
                "server": "vsphere.local",
                "username": "alice",
                "password": "s3cr3t",
                "datacenter": "dc1",
                "control-node-selector": "gcp.io/my-control-node=",
            }
        )

        provider_messages = {r.message for r in caplog.records if "provider" in r.filename}
        storage_messages = {r.message for r in caplog.records if "storage" in r.filename}

        assert provider_messages == {
            "Adding provider tolerations from control-plane",
            "Applying provider secret data for server vsphere.local",
            "Applying provider ConfigMap Data for vcenter dc1",
            'Applying provider Control Node Selector as gcp.io/my-control-node: ""',
        }
        assert storage_messages == {
            "Setting CSIMigration to false",
            "Creating storage secret data for server vsphere.local",
            "Creating storage class csi-vsphere-default",
            'Applying storage Control Node Selector as gcp.io/my-control-node: ""',
            "Adding storage tolerations from control-plane",
            "Setting storage deployment replicas to 2",
        }

        caplog.clear()
        harness.update_config(
            {
                "server": "",
                "username": "",
                "password": "",
                "datacenter": "",
                "control-node-selector": "",
                "image-registry": "dockerhub.io",
            }
        )
        provider_messages = {r.message for r in caplog.records if "provider" in r.filename}
        storage_messages = {r.message for r in caplog.records if "storage" in r.filename}

        assert provider_messages == {
            "Adding provider tolerations from control-plane",
            "Applying provider secret data for server 1.2.3.4",
            "Applying provider ConfigMap Data for vcenter Elbonia",
            'Applying provider Control Node Selector as juju-application: "kubernetes-control-plane"',
        }
        assert storage_messages == {
            "Setting CSIMigration to false",
            "Creating storage secret data for server 1.2.3.4",
            "Creating storage class csi-vsphere-default",
            'Applying storage Control Node Selector as juju-application: "kubernetes-control-plane"',
            "Setting storage deployment replicas to 2",
            "Adding storage tolerations from control-plane",
        }


def test_install_or_upgrade_apierror(harness: Harness, lk_client, api_error_klass):
    lk_client.apply.side_effect = [mock.MagicMock(), api_error_klass]
    harness.begin_with_initial_hooks()
    charm = harness.charm
    charm.stored.config_hash = "mock_hash"
    mock_event = mock.MagicMock()
    charm._install_or_upgrade(mock_event)
    mock_event.defer.assert_called_once()
    assert isinstance(charm.unit.status, WaitingStatus)


@pytest.mark.parametrize(
    "releases",
    [
        {},  # Exercise the effective defaults, not just explicit overrides.
        {"provider-release": "v1.35", "storage-release": "v3.5.1"},
        {"provider-release": "v1.36", "storage-release": "v3.6.5"},
        {"provider-release": "v1.37", "storage-release": "v3.7.5"},
    ],
)
@pytest.mark.usefixtures("integrator", "certificates", "kube_control")
def test_real_bundles_preserve_charm_configuration(harness, releases):
    """New upstream bundles must still accept credentials, scheduling and registry patches."""
    harness.begin()
    harness.update_config(
        {
            "server": "vsphere.local",
            "username": "alice",
            "password": "s3cr3t",
            "datacenter": "dc1",
            "control-node-selector": "juju-application=kubernetes-control-plane",
            **releases,
        }
    )
    resources = {
        (wrapped.resource.kind, wrapped.resource.metadata.name): wrapped.resource
        for manifests in harness.charm.collector.manifests.values()
        for wrapped in manifests.resources
    }
    provider_secret = resources["Secret", "vsphere-cloud-secret"]
    assert provider_secret.stringData == {
        "vsphere.local.username": "alice",
        "vsphere.local.password": "s3cr3t",
    }
    provider_config = yaml.safe_load(
        resources["ConfigMap", "vsphere-cloud-config"].data["vsphere.conf"]
    )
    assert provider_config["vcenter"]["dc1"]["server"] == "vsphere.local"
    storage_secret = resources["Secret", "vsphere-config-secret"]
    secret_config = base64.b64decode(storage_secret.data["csi-vsphere.conf"]).decode()
    assert '[VirtualCenter "vsphere.local"]' in secret_config
    assert 'user = "alice"' in secret_config
    assert 'password = "s3cr3t"' in secret_config
    assert 'datacenters = "dc1"' in secret_config

    for kind, name in (
        ("DaemonSet", "vsphere-cloud-controller-manager"),
        ("Deployment", "vsphere-csi-controller"),
    ):
        pod_spec = resources[kind, name].spec.template.spec
        assert pod_spec.nodeSelector == {"juju-application": "kubernetes-control-plane"}
        assert any(t.key == "node-role.kubernetes.io/control-plane" for t in pod_spec.tolerations)
    assert resources["Deployment", "vsphere-csi-controller"].spec.replicas == 2
    feature_states = resources["ConfigMap", "internal-feature-states.csi.vsphere.vmware.com"]
    assert feature_states.data["csi-migration"] == "false"
    storage_class = resources["StorageClass", "csi-vsphere-default"]
    assert storage_class.parameters == {"storagepolicyname": "vSAN Default Storage Policy"}

    for (kind, _), resource in resources.items():
        if kind in ("DaemonSet", "Deployment"):
            for container in resource.spec.template.spec.containers:
                assert container.image.startswith("ghcr.io/canonical/cdk/")


def _legacy_stored_hash(charm):
    """Hash the manifest config the way the revision without a schema default stored it.

    Before `provider-release`/`storage-release` gained a charmcraft default, an unset
    option left `config["release"]` as None, so the stored hash was identical no matter
    which release the bundled version file selected.
    """
    total = 0
    for controller in charm.collector.manifests.values():
        legacy = dict(controller.config)
        legacy["release"] = None
        with mock.patch.object(type(controller), "config", mock.PropertyMock(return_value=legacy)):
            total += controller.hash()
    return total


def _image_identity(image):
    """Reduce an image to name:tag, so rewritten registry prefixes compare equal."""
    return image.rsplit("/", 1)[-1]


def _bundled_images(controller):
    """Collect the container images the current release's manifest files declare."""
    bundle = controller.manifest_path / controller.current_release
    images = set()
    for path in sorted(bundle.glob("*.yaml")):
        for resource in yaml.safe_load_all(path.read_text()):
            if not resource or resource.get("kind") not in ("DaemonSet", "Deployment"):
                continue
            for container in resource["spec"]["template"]["spec"]["containers"]:
                images.add(_image_identity(container["image"]))
    return images


def test_schema_default_matches_version_file():
    """The charmcraft config default and the bundled version file must select the same release."""
    options = yaml.safe_load(Path("charmcraft.yaml").read_text())["config"]["options"]
    for option, version_file in (
        ("provider-release", "upstream/cloud_provider/version"),
        ("storage-release", "upstream/cloud_storage/version"),
    ):
        default = options[option]["default"]
        assert default == Path(version_file).read_text().strip()
        assert Path(f"{Path(version_file).parent}/manifests/{default}").is_dir()


@pytest.mark.usefixtures("integrator", "certificates", "kube_control")
def test_refresh_rolls_out_new_schema_default(harness, lk_client):
    """A refresh with no release configured must re-apply manifests for the new defaults."""
    harness.begin()
    harness.update_config(
        {
            "server": "vsphere.local",
            "username": "alice",
            "password": "s3cr3t",
            "datacenter": "dc1",
            "control-node-selector": "juju-application=kubernetes-control-plane",
        }
    )
    charm = harness.charm

    # Emulate the previously deployed revision, which stored a release-insensitive hash.
    legacy_hash = _legacy_stored_hash(charm)
    assert legacy_hash != charm.stored.config_hash, (
        "the stored hash must change when the schema default moves, "
        "otherwise a default-only refresh is skipped"
    )
    charm.stored.config_hash = legacy_hash
    charm.stored.deployed = True
    lk_client.apply.reset_mock()

    charm.on.config_changed.emit()

    assert charm.stored.deployed
    assert charm.stored.config_hash != legacy_hash
    applied = [call.args[0] for call in lk_client.apply.call_args_list]
    applied_images = {
        _image_identity(container.image)
        for resource in applied
        if resource.kind in ("DaemonSet", "Deployment")
        for container in resource.spec.template.spec.containers
    }
    for controller in charm.collector.manifests.values():
        assert controller.current_release == controller.default_release
        bundled = _bundled_images(controller)
        assert bundled, f"no images found in bundled {controller.name} manifests"
        assert bundled <= applied_images, (
            f"{controller.name} release {controller.current_release} was not rolled out; "
            f"missing {sorted(bundled - applied_images)}"
        )
