# Copyright 2012 OpenStack Foundation
# Copyright 2013 IBM Corp.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

# NOTE(soliosg) Do not edit this file. It will only stay temporarily
# in ironic, while QA refactors the tempest.scenario interface. This
# file was copied from openstack/tempest/tempest/scenario/manager.py,
# openstack/tempest commit: 82a278e88c9e9f9ba49f81c1f8dba0bca7943daf

from oslo_log import log
from oslo_utils import netutils
from tempest import config
from tempest import exceptions
from tempest.lib.common.utils import data_utils
from tempest.lib.common.utils import test_utils
from tempest.lib import exceptions as lib_exc
import tempest.test

CONF = config.CONF

LOG = log.getLogger(__name__)


class ScenarioTest(tempest.scenario.manager.ScenarioTest):
    """Base class for scenario tests. Uses tempest own clients. """

    credentials = ['primary', 'admin', 'system_admin']

    @classmethod
    def setup_clients(cls):
        super(ScenarioTest, cls).setup_clients()
        # Clients (in alphabetical order)
        cls.flavors_client = cls.os_primary.flavors_client
        cls.compute_floating_ips_client = (
            cls.os_primary.compute_floating_ips_client)
        if CONF.service_available.glance:
            # Check if glance v1 is available to determine which client to use.
            if CONF.image_feature_enabled.api_v1:
                cls.image_client = cls.os_primary.image_client
            elif CONF.image_feature_enabled.api_v2:
                cls.image_client = cls.os_primary.image_client_v2
            else:
                raise lib_exc.InvalidConfiguration(
                    'Either api_v1 or api_v2 must be True in '
                    '[image-feature-enabled].')
        # Compute image client
        cls.compute_images_client = cls.os_primary.compute_images_client
        cls.keypairs_client = cls.os_primary.keypairs_client
        # Nova security groups client
        cls.compute_security_groups_client = (
            cls.os_primary.compute_security_groups_client)
        cls.compute_security_group_rules_client = (
            cls.os_primary.compute_security_group_rules_client)
        cls.servers_client = cls.os_primary.servers_client
        cls.interface_client = cls.os_primary.interfaces_client
        # Neutron network client
        cls.networks_client = cls.os_primary.networks_client
        cls.ports_client = cls.os_primary.ports_client
        cls.routers_client = cls.os_primary.routers_client
        cls.subnets_client = cls.os_primary.subnets_client
        cls.floating_ips_client = cls.os_primary.floating_ips_client
        cls.security_groups_client = cls.os_primary.security_groups_client
        cls.security_group_rules_client = (
            cls.os_primary.security_group_rules_client)

        cls.volumes_client = cls.os_primary.volumes_client_latest
        cls.snapshots_client = cls.os_primary.snapshots_client_latest

    # ## Test functions library
    #
    # The create_[resource] functions only return body and discard the
    # resp part which is not used in scenario tests

    def create_floating_ip(self, thing, pool_name=None):
        """Create a floating IP and associates to a server on Nova"""

        if not pool_name:
            pool_name = CONF.network.floating_network_name
        client = self.os_primary.compute_floating_ips_client
        floating_ip = (client.
                       create_floating_ip(pool=pool_name)['floating_ip'])
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        client.delete_floating_ip,
                        floating_ip['id'])
        client.associate_floating_ip_to_server(
            floating_ip['ip'], thing['id'])
        return floating_ip

    def create_timestamp(self, ip_address, dev_name=None, mount_path='/mnt',
                         private_key=None):
        ssh_client = self.get_remote_client(ip_address,
                                            private_key=private_key)
        if dev_name is not None:
            ssh_client.make_fs(dev_name)
            ssh_client.exec_command('sudo mount /dev/%s %s' % (dev_name,
                                                               mount_path))
        cmd_timestamp = 'sudo sh -c "date > %s/timestamp; sync"' % mount_path
        ssh_client.exec_command(cmd_timestamp)
        timestamp = ssh_client.exec_command('sudo cat %s/timestamp'
                                            % mount_path)
        if dev_name is not None:
            ssh_client.exec_command('sudo umount %s' % mount_path)
        return timestamp

    def get_server_ip(self, server):
        """Get the server fixed or floating IP.

        Based on the configuration we're in, return a correct ip
        address for validating that a guest is up.
        """
        if CONF.validation.connect_method == 'floating':
            # The tests calling this method don't have a floating IP
            # and can't make use of the validation resources. So the
            # method is creating the floating IP there.
            return self.create_floating_ip(server)['ip']
        elif CONF.validation.connect_method == 'fixed':
            # Determine the network name to look for based on config or creds
            # provider network resources.
            if CONF.validation.network_for_ssh:
                addresses = server['addresses'][
                    CONF.validation.network_for_ssh]
            else:
                creds_provider = self._get_credentials_provider()
                net_creds = creds_provider.get_primary_creds()
                network = getattr(net_creds, 'network', None)
                addresses = (server['addresses'][network['name']]
                             if network else [])
            for address in addresses:
                if (address['version'] == CONF.validation.ip_version_for_ssh
                        and address['OS-EXT-IPS:type'] == 'fixed'):
                    return address['addr']
            raise exceptions.ServerUnreachable(server_id=server['id'])
        else:
            raise lib_exc.InvalidConfiguration()

    def _get_router(self, client=None, tenant_id=None):
        """Retrieve a router for the given tenant id.

        If a public router has been configured, it will be returned.

        If a public router has not been configured, but a public
        network has, a tenant router will be created and returned that
        routes traffic to the public network.
        """
        if not client:
            client = self.os_primary.routers_client
        if not tenant_id:
            tenant_id = client.tenant_id
        router_id = CONF.network.public_router_id
        network_id = CONF.network.public_network_id
        if router_id:
            body = client.show_router(router_id)
            return body['router']
        elif network_id:
            router = self._create_router(client, tenant_id)
            kwargs = {'external_gateway_info': dict(network_id=network_id)}
            router = client.update_router(router['id'], **kwargs)['router']
            return router
        else:
            raise Exception("Neither of 'public_router_id' or "
                            "'public_network_id' has been defined.")

    def _create_router(self, client=None, tenant_id=None,
                       namestart='router-smoke'):
        if not client:
            client = self.os_primary.routers_client
        if not tenant_id:
            tenant_id = client.tenant_id
        name = data_utils.rand_name(namestart)
        result = client.create_router(name=name,
                                      admin_state_up=True,
                                      tenant_id=tenant_id)
        router = result['router']
        self.assertEqual(router['name'], name)
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        client.delete_router,
                        router['id'])
        return router


class NetworkScenarioTest(ScenarioTest):
    """Base class for network scenario tests.

    This class provide helpers for network scenario tests, using the neutron
    API. Helpers from ancestor which use the nova network API are overridden
    with the neutron API.

    This Class also enforces using Neutron instead of novanetwork.
    Subclassed tests will be skipped if Neutron is not enabled

    """

    credentials = ['primary', 'admin', 'system_admin']

    @classmethod
    def skip_checks(cls):
        super(NetworkScenarioTest, cls).skip_checks()
        if not CONF.service_available.neutron:
            raise cls.skipException('Neutron not available')

    def _create_network(self, networks_client=None,
                        tenant_id=None,
                        namestart='network-smoke-',
                        port_security_enabled=True):
        if not networks_client:
            networks_client = self.os_primary.networks_client
        if not tenant_id:
            tenant_id = self.os_primary.networks_client.tenant_id
        name = data_utils.rand_name(namestart)
        network_kwargs = dict(name=name, tenant_id=tenant_id)
        # Neutron disables port security by default so we have to check the
        # config before trying to create the network with port_security_enabled
        if CONF.network_feature_enabled.port_security:
            network_kwargs['port_security_enabled'] = port_security_enabled
        result = networks_client.create_network(**network_kwargs)
        network = result['network']

        self.assertEqual(network['name'], name)
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        networks_client.delete_network,
                        network['id'])
        return network

    def _get_server_port_id_and_ip4(self, server, ip_addr=None):
        if ip_addr:
            ports = self.os_admin.ports_client.list_ports(
                device_id=server['id'],
                fixed_ips='ip_address=%s' % ip_addr)['ports']
        else:
            ports = self.os_admin.ports_client.list_ports(
                device_id=server['id'])['ports']
        # A port can have more than one IP address in some cases.
        # If the network is dual-stack (IPv4 + IPv6), this port is associated
        # with 2 subnets
        p_status = ['ACTIVE']
        # NOTE(vsaienko) With Ironic, instances live on separate hardware
        # servers. Neutron does not bind ports for Ironic instances, as a
        # result the port remains in the DOWN state.
        # TODO(vsaienko) remove once bug: #1599836 is resolved.
        if getattr(CONF.service_available, 'ironic', False):
            p_status.append('DOWN')
        port_map = [(p["id"], fxip["ip_address"])
                    for p in ports
                    for fxip in p["fixed_ips"]
                    if netutils.is_valid_ipv4(fxip["ip_address"])
                    and p['status'] in p_status]
        inactive = [p for p in ports if p['status'] != 'ACTIVE']
        if inactive:
            LOG.warning("Instance has ports that are not ACTIVE: %s", inactive)

        self.assertNotEqual(0, len(port_map),
                            "No IPv4 addresses found in: %s" % ports)
        self.assertEqual(len(port_map), 1,
                         "Found multiple IPv4 addresses: %s. "
                         "Unable to determine which port to target."
                         % port_map)
        return port_map[0]

    def create_floating_ip(self, thing, external_network_id=None,
                           port_id=None, client=None):
        """Create a floating IP and associates to a resource/port on Neutron"""
        if not external_network_id:
            external_network_id = CONF.network.public_network_id
        if not client:
            client = self.os_primary.floating_ips_client
        if not port_id:
            port_id, ip4 = self._get_server_port_id_and_ip4(thing)
        else:
            ip4 = None
        result = client.create_floatingip(
            floating_network_id=external_network_id,
            port_id=port_id,
            tenant_id=thing['tenant_id'],
            fixed_ip_address=ip4
        )
        floating_ip = result['floatingip']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        client.delete_floatingip,
                        floating_ip['id'])
        return floating_ip
