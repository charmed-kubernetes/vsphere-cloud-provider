#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Dispatch logic for the vsphere CPI operator charm."""

import json
import logging
from hashlib import md5
from typing import Optional

from charms.vsphere_cloud_provider_operator.v0.vsphere_integration import (
    VsphereIntegrationRequires,
)
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

from backend import CharmBackend
from config import CharmConfig
from kube_control_requires import KubeControlRequires

log = logging.getLogger(__name__)


class VsphereCloudProviderCharm(CharmBase):
    """Dispatch logic for the VpshereCC operator charm."""

    stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.stored.set_default(config_hash=None, deployed=False)

        self.backend = CharmBackend(self)
        self.integrator = VsphereIntegrationRequires(self)
        self.kube_control = KubeControlRequires(self)

        self.framework.observe(self.on.kube_control_relation_created, self._check_config)
        self.framework.observe(self.on.kube_control_relation_joined, self._kube_control)
        self.framework.observe(self.on.kube_control_relation_changed, self._check_config)
        self.framework.observe(self.on.kube_control_relation_broken, self._check_config)

        self.framework.observe(
            self.on.external_cloud_provider_relation_created, self._check_config
        )
        self.framework.observe(self.on.external_cloud_provider_relation_broken, self._check_config)

        self.framework.observe(self.on.vsphere_integration_relation_created, self._check_config)
        self.framework.observe(self.on.vsphere_integration_relation_joined, self._check_config)
        self.framework.observe(self.on.vsphere_integration_relation_changed, self._check_config)
        self.framework.observe(self.on.vsphere_integration_relation_broken, self._check_config)

        self.framework.observe(self.on.install, self._install_or_upgrade)
        self.framework.observe(self.on.upgrade_charm, self._install_or_upgrade)
        self.framework.observe(self.on.config_changed, self._check_config)
        self.framework.observe(self.on.leader_elected, self._set_version)
        self.framework.observe(self.on.stop, self._cleanup)

    @property
    def control_plane_relation(self) -> Optional[Relation]:
        """Find a control-plane-node external-cloud-provider relation."""
        return self.model.get_relation("external-cloud-provider")

    def _kube_control(self, event=None):
        self.kube_control.set_auth_request(self.app.name)
        return self._check_config(event)

    def _check_config(self, event=None):
        self.unit.status = MaintenanceStatus("Evaluating kubernetes authentication.")
        evaluation = self.kube_control.evaluate_relation(event)
        if evaluation:
            if "Waiting" in evaluation:
                self.unit.status = WaitingStatus(evaluation)
            else:
                self.unit.status = BlockedStatus(evaluation)
            return
        self.kube_control.create_kubeconfig("/root/.kube/config", "root")
        self.kube_control.create_kubeconfig("/home/ubuntu/.kube/config", "ubuntu")

        self.unit.status = MaintenanceStatus("Evaluating cloud-config.")
        cloud_config = CharmConfig(self)
        evaluation = cloud_config.evaluate_relation(event)
        if evaluation:
            self.stored.config_hash = None
            if "Waiting" in evaluation:
                self.unit.status = WaitingStatus(evaluation)
            else:
                self.unit.status = BlockedStatus(evaluation)
            return

        if err := cloud_config.validate_cloud_config:
            self.unit.status = BlockedStatus(f"Invalid config: {err}")
            return
        new_hash = md5(
            json.dumps(cloud_config.properties, sort_keys=True).encode("utf8")
        ).hexdigest()
        if new_hash == self.stored.config_hash:
            # No change
            self.unit.status = ActiveStatus()
            return
        self.stored.config_hash = new_hash
        self.backend.apply_cloud_config(cloud_config)
        if not self.stored.deployed:
            self._install_or_upgrade()
        else:
            self.backend.restart()
            self.unit.status = ActiveStatus()

    def _install_or_upgrade(self, _event=None):
        if not self.stored.config_hash:
            return
        self.unit.status = MaintenanceStatus("Deploying vSphere Cloud Provider")
        self.backend.apply_statics()
        self.stored.deployed = True
        self.unit.status = ActiveStatus()
        self._set_version()

    def _set_version(self, _event=None):
        if self.unit.is_leader():
            _, version = self.config.get("image").rsplit(":")
            self.unit.set_workload_version(version)

    def _cleanup(self, _event):
        self.unit.status = MaintenanceStatus("Cleaning up vSphere Cloud Provider")
        self.backend.remove()
        self.unit.status = MaintenanceStatus("Shutting down")


if __name__ == "__main__":
    main(VsphereCloudProviderCharm)
