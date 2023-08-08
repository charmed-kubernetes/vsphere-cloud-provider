# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere-integration interface.

This only implements the requires side, currently, since the integrator
is still using the Reactive Charm framework self.
"""
import logging
from typing import Optional

from backports.cached_property import cached_property
from ops.charm import RelationBrokenEvent
from ops.framework import Object
from pydantic import BaseModel, Extra, StrictStr, ValidationError

log = logging.getLogger(__name__)


class VsphereIntegrationData(BaseModel, extra=Extra.allow):
    """Requires side of schema of vsphere-integration relation."""

    datacenter: StrictStr
    datastore: Optional[StrictStr]
    folder: Optional[StrictStr]
    password: StrictStr
    respool_path: Optional[StrictStr]
    user: StrictStr
    vsphere_ip: StrictStr


class VsphereIntegrationRequires(Object):
    """Requires side of vsphere-integration relation."""

    LIMIT = 1

    def __init__(self, charm, endpoint="vsphere-integration"):
        super().__init__(charm, f"relation-{endpoint}")
        self.charm = charm
        self.endpoint = endpoint

    @cached_property
    def relation(self):
        """The relation to the integrator, or None."""
        return self.model.get_relation(self.endpoint)

    @cached_property
    def _raw_data(self):
        if self.relation and self.relation.units:
            return self.relation.data[list(self.relation.units)[0]]
        return None

    @cached_property
    def _data(self) -> Optional[VsphereIntegrationData]:
        raw = self._raw_data
        return VsphereIntegrationData(**raw) if raw else None

    def evaluate_relation(self, event) -> Optional[str]:
        """Determine if relation is ready."""
        no_relation = not self.relation or (
            isinstance(event, RelationBrokenEvent) and event.relation is self.relation
        )
        if not self.is_ready:
            if no_relation:
                return f"Missing required {self.endpoint} relation"
            return f"Waiting for {self.endpoint} relation"
        return None

    @property
    def is_ready(self) -> bool:
        """Whether the request for this instance has been completed."""
        try:
            self._data
        except ValidationError as ve:
            log.error(f"{self.endpoint} relation data not yet valid. ({ve}")
            return False
        if self._data is None:
            log.error(f"{self.endpoint} relation data not yet available.")
            return False
        return True

    @property
    def datacenter(self) -> Optional[str]:
        """The datacenter value."""
        if not self.is_ready:
            return None
        return self._data.datacenter

    @property
    def datastore(self) -> Optional[str]:
        """The datastore value."""
        if not self.is_ready:
            return None
        return self._data.datastore

    @property
    def folder(self) -> Optional[str]:
        """The folder value."""
        if not self.is_ready:
            return None
        return self._data.folder

    @property
    def user(self) -> Optional[str]:
        """The user value."""
        if not self.is_ready:
            return None
        return self._data.user

    @property
    def password(self) -> Optional[str]:
        """The password value."""
        if not self.is_ready:
            return None
        return self._data.password

    @property
    def respool_path(self) -> Optional[str]:
        """The respool_path value."""
        if not self.is_ready:
            return None
        return self._data.respool_path

    @property
    def vsphere_ip(self) -> Optional[str]:
        """The vsphere_ip value."""
        if not self.is_ready:
            return None
        return self._data.vsphere_ip
