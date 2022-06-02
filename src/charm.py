#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Dispatch logic for the vsphere CPI operator charm."""

import logging
from pathlib import Path
from typing import Optional

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    Relation,
    WaitingStatus,
)

from config import CharmConfig
from requires_certificates import CertificatesRequires
from requires_kube_control import KubeControlRequires
from requires_vsphere_integration import VsphereIntegrationRequires
from vsphere_manifests import VsphereManifests

log = logging.getLogger(__name__)


class VsphereCloudProviderCharm(CharmBase):
    """Dispatch logic for the VpshereCC operator charm."""

    CA_CERT_PATH = Path("/srv/kubernetes/ca.crt")

    stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        # Relation Validator and datastore
        self.integrator = VsphereIntegrationRequires(self)
        self.kube_control = KubeControlRequires(self)
        self.certificates = CertificatesRequires(self)
        # Config Validator and datastore
        self.charm_config = CharmConfig(self)

        self.CA_CERT_PATH.parent.mkdir(exist_ok=True)
        self.stored.set_default(
            config_hash=None,  # hashed value of the manifest_config once valid
            deployed=False,  # True if the config has been applied after new hash
        )
        self.manifests = VsphereManifests(
            self.app.name, self.charm_config, self.integrator, self.control_plane_relation
        )

        self.framework.observe(self.on.kube_control_relation_created, self._kube_control)
        self.framework.observe(self.on.kube_control_relation_joined, self._kube_control)
        self.framework.observe(self.on.kube_control_relation_changed, self._merge_config)
        self.framework.observe(self.on.kube_control_relation_broken, self._merge_config)

        self.framework.observe(self.on.certificates_relation_created, self._merge_config)
        self.framework.observe(self.on.certificates_relation_changed, self._merge_config)
        self.framework.observe(self.on.certificates_relation_broken, self._merge_config)

        self.framework.observe(self.on.external_cloud_provider_relation_joined, self._merge_config)
        self.framework.observe(self.on.external_cloud_provider_relation_broken, self._merge_config)

        self.framework.observe(self.on.vsphere_integration_relation_joined, self._merge_config)
        self.framework.observe(self.on.vsphere_integration_relation_changed, self._merge_config)
        self.framework.observe(self.on.vsphere_integration_relation_broken, self._merge_config)

        self.framework.observe(self.on.list_versions_action, self._list_versions)
        self.framework.observe(self.on.list_resources_action, self._list_resources)
        self.framework.observe(self.on.scrub_resources_action, self._scrub_resources)
        self.framework.observe(self.on.update_status, self._update_status)

        self.framework.observe(self.on.install, self._install_or_upgrade)
        self.framework.observe(self.on.upgrade_charm, self._install_or_upgrade)
        self.framework.observe(self.on.config_changed, self._merge_config)
        self.framework.observe(self.on.stop, self._cleanup)

    def _list_versions(self, event):
        result = {
            "versions": "\n".join(sorted(str(_) for _ in self.manifests.releases)),
        }
        event.set_results(result)

    def _list_resources(self, event):
        res_filter = [_.lower() for _ in event.params.get("resources", "").split()]
        if res_filter:
            event.log(f"Filter resource listing with {res_filter}")
        current = self.manifests.active_resources()
        expected = self.manifests.expected_resources()
        correct, extra, missing = (
            set(),
            set(),
            set(),
        )
        for kind_ns, current_set in current.items():
            if not res_filter or kind_ns.kind.lower() in res_filter:
                expected_set = expected[kind_ns]
                correct |= current_set & expected_set
                extra |= current_set - expected_set
                missing |= expected_set - current_set

        result = {
            "correct": "\n".join(sorted(str(_) for _ in correct)),
            "extra": "\n".join(sorted(str(_) for _ in extra)),
            "missing": "\n".join(sorted(str(_) for _ in missing)),
        }
        result = {k: v for k, v in result.items() if v}
        event.set_results(result)
        return correct, extra, missing

    def _scrub_resources(self, event):
        _, extra, __ = self._list_resources(event)
        if extra:
            self.manifests.delete_resources(*extra)
            self._list_resources(event)

    def _update_status(self, _):
        if not self.stored.deployed:
            return

        unready = []
        for resource in self.manifests.status():
            for cond in resource.status.conditions:
                if cond.status != "True":
                    unready.append(f"{resource} not {cond.type}")
        if unready:
            self.unit.status = WaitingStatus(", ".join(sorted(unready)))
        else:
            self.unit.status = ActiveStatus("Ready")
            self.unit.set_workload_version(self.manifests.current_release)

    @property
    def control_plane_relation(self) -> Optional[Relation]:
        """Find a control-plane-node external-cloud-provider relation."""
        return self.model.get_relation("external-cloud-provider")

    def _kube_control(self, event=None):
        self.kube_control.set_auth_request(self.unit.name)
        return self._merge_config(event)

    def _check_kube_control(self, event):
        self.unit.status = MaintenanceStatus("Evaluating kubernetes authentication.")
        evaluation = self.kube_control.evaluate_relation(event)
        if evaluation:
            if "Waiting" in evaluation:
                self.unit.status = WaitingStatus(evaluation)
            else:
                self.unit.status = BlockedStatus(evaluation)
            return False
        if not self.kube_control.get_auth_credentials(self.unit.name):
            self.unit.status = WaitingStatus("Waiting for kube-control: unit credentials")
            return False
        self.kube_control.create_kubeconfig(
            self.CA_CERT_PATH, "/root/.kube/config", "root", self.unit.name
        )
        self.kube_control.create_kubeconfig(
            self.CA_CERT_PATH, "/home/ubuntu/.kube/config", "ubuntu", self.unit.name
        )
        return True

    def _check_certificates(self, event):
        self.unit.status = MaintenanceStatus("Evaluating certificates.")
        evaluation = self.certificates.evaluate_relation(event)
        if evaluation:
            if "Waiting" in evaluation:
                self.unit.status = WaitingStatus(evaluation)
            else:
                self.unit.status = BlockedStatus(evaluation)
            return False
        self.CA_CERT_PATH.write_text(self.certificates.ca)
        return True

    def _check_vsphere_relation(self, event):
        self.unit.status = MaintenanceStatus("Evaluating vsphere.")
        evaluation = self.integrator.evaluate_relation(event)
        if evaluation:
            if "Waiting" in evaluation:
                self.unit.status = WaitingStatus(evaluation)
            else:
                self.unit.status = BlockedStatus(evaluation)
            return False
        return True

    def _check_config(self):
        self.unit.status = MaintenanceStatus("Evaluating charm config.")
        evaluation = self.charm_config.evaluate()
        if evaluation:
            self.unit.status = BlockedStatus(evaluation)
            return False
        return True

    def _merge_config(self, event=None):
        if not self._check_vsphere_relation(event):
            return

        if not self._check_certificates(event):
            return

        if not self._check_kube_control(event):
            return

        if not self._check_config():
            return

        self.unit.status = MaintenanceStatus("Evaluating Manifests")
        evaluation = self.manifests.evaluate()
        if evaluation:
            self.unit.status = BlockedStatus(evaluation)
            return

        new_hash = self.manifests.hash()
        if new_hash == self.stored.config_hash:
            return

        self.stored.config_hash = new_hash
        self.stored.deployed = False
        self._install_or_upgrade()

    def _install_or_upgrade(self, _event=None):
        if not self.stored.config_hash:
            return
        self.unit.status = MaintenanceStatus("Deploying vSphere Cloud Provider")
        self.unit.set_workload_version("")
        self.manifests.apply_manifests()
        self.stored.deployed = True

    def _cleanup(self, _event):
        self.unit.status = MaintenanceStatus("Cleaning up vSphere Cloud Provider")
        self.backend.remove()
        self.unit.status = MaintenanceStatus("Shutting down")


if __name__ == "__main__":
    main(VsphereCloudProviderCharm)
