# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Generate vsphere yaml necessary for deploying the provider from templates in the charm."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape
from lightkube import codecs

base_path = Path(__file__).parent.parent


@dataclass
class Resource:
    """Represents kubernetes resource objects as yaml or lightkube resources."""

    yaml: str

    @property
    def lightkube(self):
        """Resolve the yaml to a list of lightkube objects."""
        return codecs.load_all_yaml(self.yaml)


@dataclass
class TemplateEngine:
    """Dataclass which can create yamls based on config or relation data."""

    juju_app: str
    control_node_selector: Dict[str, str]
    server: str
    username: str
    password: str = field(repr=False)
    datacenter: str
    image: str

    _loader: FileSystemLoader = FileSystemLoader(base_path / "templates")

    def _load(self, template_name: str):
        variables = dict(
            juju_app=self.juju_app,
            server=self.server,
            username=self.username,
            password=self.password,
            datacenter=self.datacenter,
            image=self.image,
            node_selector=self.control_node_selector,
        )
        env = Environment(loader=self._loader, autoescape=select_autoescape())
        template = env.get_template(template_name)
        return Resource(template.render(**variables))

    @property
    def config_map(self) -> Resource:
        """Yields yaml for configuring the provider."""
        return self._load("cpi-config-map.yaml.j2")

    @property
    def daemonset(self) -> Resource:
        """Yields yaml for configuring the DaemonSet."""
        return self._load("cpi-daemonset.yaml.j2")

    @property
    def service(self) -> Resource:
        """Yields yaml for configuring the ServiceAccount and Service."""
        return self._load("cpi-service.yaml.j2")

    @property
    def role_bindings(self) -> Resource:
        """Yields yaml for configuring the RoleBinding and ClusterRoleBinding."""
        return self._load("cpi-role-bindings.yaml.j2")

    @property
    def roles(self) -> Resource:
        """Yields yaml for configuring the ClusterRole."""
        return self._load("cpi-roles.yaml.j2")

    @property
    def secret(self) -> Resource:
        """Yields yaml for configuring the vsphere Secret."""
        return self._load("cpi-secret.yaml.j2")
