from spaceone.inventory.libs.manager import AzureManager
from spaceone.inventory.libs.schema.base import ReferenceModel
from pprint import pprint
from spaceone.inventory.connector.network_security_group import NetworkSecurityGroupConnector
from spaceone.inventory.model.networksecuritygroup.cloud_service import *
from spaceone.inventory.model.networksecuritygroup.cloud_service_type import CLOUD_SERVICE_TYPES
from spaceone.inventory.model.networksecuritygroup.data import *
import time
import ipaddress


class NetworkSecurityGroupManager(AzureManager):
    connector_name = 'NetworkSecurityGroupConnector'
    cloud_service_types = CLOUD_SERVICE_TYPES

    def collect_cloud_service(self, params):
        print("** Network Security Group START **")
        start_time = time.time()
        """
        Args:
            params:
                - options
                - schema
                - secret_data
                - filter
                - zones
                - subscription_info
        Response:
            CloudServiceResponse
        """
        secret_data = params['secret_data']
        subscription_info = params['subscription_info']
        network_security_group_conn: NetworkSecurityGroupConnector = self.locator.get_connector(self.connector_name,**params)
        network_security_groups = []
        network_security_group_list = network_security_group_conn.list_all_network_security_groups()

        for network_security_group in network_security_group_list:
            network_security_group_dict = self.convert_nested_dictionary(self, network_security_group)

            if network_security_group_dict.get('security_rules') is not None:
                # update security rules
                inbound_rules = []
                outbound_rules = []

                # update custom security rules
                try:
                    inbound, outbound = self.split_security_rules(network_security_group_dict, 'security_rules')
                    for ib in inbound:
                        inbound_rules.append(ib)
                    for ob in outbound:
                        outbound_rules.append(ob)

                except Exception as e:
                    print(f'[ERROR: Azure Network Security Group Manager Split Security Rules]: {e}')

            # update default security rules
            if network_security_group_dict.get('default_security_rules') is not None:
                try:
                    inbound, outbound = self.split_security_rules(network_security_group_dict, 'default_security_rules')

                    for ib in inbound:
                        inbound_rules.append(ib)
                    for ob in outbound:
                        outbound_rules.append(ob)

                except ValueError as e:
                    print(f'[ERROR: Azure Network Security Group Manager Split Security Rules]: {e}')

            network_security_group_dict.update({
                'inbound_security_rules': inbound_rules,
                'outbound_security_rules': outbound_rules
            })

            # get network interfaces
            if network_security_group_dict.get('network_interfaces') is not None:
                new_network_interfaces_list, virtual_machines_display_str = self.get_network_interfaces(self, network_security_group_conn, network_security_group_dict['network_interfaces'])
                network_security_group_dict['network_interfaces'] = new_network_interfaces_list  # Remove existing list, append new list
                network_security_group_dict.update({
                    'virtual_machines_display': virtual_machines_display_str
                })

            # Change Subnet models to ID
            if network_security_group_dict.get('network_interfaces') is not None:
                self.replace_subnet_model_to_id(network_security_group_dict['network_interfaces'])

            # Get private ip address and public ip address
            if network_security_group_dict.get('network_interfaces') is not None:
                self.get_ip_addresses(network_security_group_dict['network_interfaces'])

            # Get Subnet information
            if network_security_group_dict.get('subnets') is not None:
                network_security_group_dict['subnets'] = self.get_subnet(self, network_security_group_conn, network_security_group_dict['subnets'])

                if network_security_group_dict.get('subnets'):
                    try:
                        for subnet in network_security_group_dict['subnets']:
                            subnet.update({
                                'virtual_network': self.get_virtual_network(subnet['id'])
                            })
                    except ValueError as e:
                        print(f'[ERROR: Azure Network Security Group Manager Get Virtual Network from Subnets]: {e}')

            # update application_gateway_dict
            network_security_group_dict.update({
                'resource_group': self.get_resource_group_from_id(network_security_group_dict['id']),
                # parse resource_group from ID
                'subscription_id': subscription_info['subscription_id'],
                'subscription_name': subscription_info['subscription_name'],
            })

            print(f'[NETWORK SECURITY GROUP INFO] {network_security_group_dict}')

            network_security_group_data = NetworkSecurityGroup(network_security_group_dict, strict=False)
            network_security_group_resource = NetworkSecurityGroupResource({
                'data': network_security_group_data,
                'region_code': network_security_group_data.location,
                'reference': ReferenceModel(network_security_group_data.reference()),
                'name': network_security_group_data.name
            })

            # Must set_region_code method for region collection
            self.set_region_code(network_security_group_data['location'])
            network_security_groups.append(NetworkSecurityGroupResponse({'resource': network_security_group_resource}))

        print(f'** Network Security Group Finished {time.time() - start_time} Seconds **')
        return network_security_groups

    @staticmethod
    def get_resource_group_from_id(dict_id):
        resource_group = ''
        try:
            resource_group = dict_id.split('/')[4]
        except ValueError as e:
            print(f'[ERROR: Azure Manager Get Resource Group Info]')
        return resource_group

    @staticmethod
    def split_security_rules(network_security_group_dict, mode):
        inbound_security_rules = []
        outbound_security_rules = []
        if mode == 'security_rules':
            rule_list = network_security_group_dict['security_rules']
        elif mode == 'default_security_rules':
            rule_list = network_security_group_dict['default_security_rules']

        try:
            for security_rule in rule_list:
                if security_rule.get('direction', '') == 'Inbound':
                    inbound_security_rules.append(security_rule)
                elif security_rule.get('direction', '') == 'Outbound':
                    outbound_security_rules.append(security_rule)
        except ValueError as e:
            print(f'[ERROR: Azure Manager Split Network Security Group Info]: {e}')

        return inbound_security_rules, outbound_security_rules

    @staticmethod
    def replace_subnet_model_to_id(network_interfaces_list):
        try:
            for network_interface in network_interfaces_list:
                if network_interface.get('ip_configurations') is not None:
                    for ip_configuration in network_interface['ip_configurations']:
                        ip_configuration['subnet'] = ip_configuration.get('subnet', {}).get('id', '')
        except Exception as e:
            print(f'[ERROR: Azure Network Security Group Manager Get Network IP Configuration Subnets]: {e}')

        return

    @staticmethod
    def get_network_interfaces(self, network_security_group_conn, network_interfaces_list):
        network_interfaces_new_list = []
        virtual_machines_display_list = []
        virtual_machines_str = ''
        try:
            for network_interface in network_interfaces_list:
                resource_group = network_interface['id'].split('/')[4]
                network_interface_name = network_interface['id'].split('/')[8]
                network_interface_obj = network_security_group_conn.get_network_interfaces(network_interface_name, resource_group)
                network_interface_dict = self.convert_nested_dictionary(self, network_interface_obj)

                if network_interface_dict['id'] == network_interface['id']:
                    # Get virtual machine display
                    if network_interface_dict.get('virtual_machine') is not None:
                        try:
                            virtual_machine_display = network_interface_dict['virtual_machine']['id'].split('/')[8]
                            virtual_machines_display_list.append(virtual_machine_display)
                            network_interface_dict.update({
                                'virtual_machine_display': virtual_machine_display
                            })
                        except Exception as e:
                            print(f'[ERROR: Azure Network Security Group Manager Get Virtual Machine]: {e}')

                    network_interfaces_new_list.append(network_interface_dict)
                    virtual_machines_str = ', '.join(virtual_machines_display_list)
            return network_interfaces_new_list, virtual_machines_str

        except Exception as e:
            print(f'[ERROR: Azure Network Security Group Manager Network Interface List]: {e}')

    @staticmethod
    def get_ip_addresses(network_interfaces_list):
        if network_interfaces_list is not []:
            try:
                for network_interface in network_interfaces_list:
                    if network_interface.get('ip_configurations') is not None:
                        for ip_configuration in network_interface['ip_configurations']:
                            private_ip_address = ip_configuration['private_ip_address']
                            network_interface.update({
                                'private_ip_address': private_ip_address
                            })

                            if ip_configuration.get('public_ip_address') is not None:
                                try:
                                    public_ip_address = ip_configuration['public_ip_address']['id'].split('/')[8]
                                    network_interface.update({
                                        'public_ip_address': public_ip_address,
                                    })
                                except Exception as e:
                                    print(f'[ERROR: Azure Network Security Group Manager Get Public IP Addresses]: {e}')



            except Exception as e:
                print(f'[ERROR: Azure Network Security Group Manager Get IP Addresses]: {e}')
        return

    @staticmethod
    def get_subnet(self, network_security_group_conn, subnets_list):
        subnets_full_list = []
        if subnets_list is not []:
            try:
                for subnet in subnets_list:
                    try:
                        resource_group_name = subnet['id'].split('/')[4]
                        subnet_name = subnet['id'].split('/')[10]
                        virtual_network_name = subnet['id'].split('/')[8]

                        subnet_obj = network_security_group_conn.get_subnet(resource_group_name, subnet_name, virtual_network_name)
                        subnet_dict = self.convert_nested_dictionary(self, subnet_obj)
                        subnets_full_list.append(subnet_dict)
                    except ValueError as e:
                        print(f'[ERROR: Azure Network Security Group Manager Split Subnet Info]: {e}')
                return subnets_full_list

            except Exception as e:
                print(f'[ERROR: Azure Network Security Group Manager Get Subnets]: {e}')
        return

    @staticmethod
    def get_virtual_network(subnet_id):
        try:
            virtual_network = subnet_id.split('/')[8]
            return virtual_network

        except Exception as e:
            print(f'[ERROR: Azure Network Security Group Manager Get Virtual Network Name from Subnets]: {e}')
