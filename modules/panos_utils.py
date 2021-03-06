#!/usr/bin/env python3

import panos
import panos.device
import panos.firewall
import panos.objects
import panos.policies
import xml.etree.ElementTree as etree

class PanosUtils:
  def __init__(self, **kwargs):
    if not kwargs == None:
      for key, value in kwargs.items():
        setattr(self, key, value)
    
  def connect_to_fw(self, hostname, api_key, vsys=None):
    try:
      fw = panos.firewall.Firewall(
        hostname = hostname,
        api_key = api_key,
        vsys = vsys
      )
      fw.refresh_system_info()
    except Exception as e:
      self.utils.log.error(f"Could not connect to firewall: { e }")
      raise(e)
    else:
      return fw

  def get_yaml_conf(self, force_overwrite):
    fw_configs = self.get_configs_from_all_firewalls()

    for hostname in fw_configs:
      for vsys in fw_configs[hostname]:
        modules = fw_configs[hostname][vsys]['config_modules']
        for module in modules:
          for object_type in modules[module]:
            file_params = {
              "conf_dir": f"{ hostname }/{ vsys }",
              "filename": f"/{ module }_{ object_type }",
              "force_overwrite": force_overwrite
            }
            data = modules[module][object_type]
            if len(data) > 0:
              # don't write blank configs
              self.utils.write_host_config_file(data, file_params)

  def get_configs_from_all_firewalls(self, return_object=False):
    fw_configs = {}
    for host in self.utils.config['hosts']:
      self.utils.log.info(f"Getting config for host: { host['hostname'] }")
      if host.get('api_key', None) is None:
        continue

      vsys_list = self.utils.get_hostname_vsys(host['hostname'])
      for vsys in vsys_list:
        self.utils.log.info(f"Getting config for vsys: { vsys }")
        conn = {
          "hostname": host['hostname'],
          "host_args": host,
          "add": False,
          "return_object": return_object
        }
        if (len(vsys_list) < 2) and (vsys == 'vsys1'):
          # on device that only has one vsys
          conn_vsys = None
        else:
          conn_vsys = vsys
        
        try:
          vsys_conn = self.connect_to_fw(host['hostname'],
                                         self.fix_api_key(host['api_key']),
                                         conn_vsys)
        except:
          continue

        conn['vsys'] = vsys_conn
        conn['rulebase'] = panos.policies.Rulebase()
        conn['vsys'].add(conn['rulebase'])

        fw_config = self.get_modules_from_firewall(conn)
        fw_configs[host['hostname']] = {}
        fw_configs[host['hostname']][vsys] = {}
        fw_configs[host['hostname']][vsys]['conn'] = conn
        fw_configs[host['hostname']][vsys]['config_modules'] = fw_config
    return fw_configs

  def get_modules_from_firewall(self, conn):
    modules = self.utils.api_params['modules']
    modules_config = {}
    for module in modules:
      self.utils.log.debug(f"Getting module config for: { module }")
      modules_config[module] = self.get_objects_from_firewall(conn, 
                                                              modules[module])
    return modules_config
  
  def get_objects_from_firewall(self, conn, module):
    objects_config = {}
    for object_type in module:
      if module[object_type]['skip']:
        continue

      object_info = module[object_type]
      object_class = self.utils.class_for_name(object_info['module'],
                                               object_info['class'])
      object_data = self.get_object_from_firewall(conn, object_info, 
                                                  object_class)
      objects_config[object_type] = object_data
    return objects_config
      
  def get_object_from_firewall(self, conn, object_info, object_class):
    object_data = object_class.refreshall(conn[object_info['parent']],
                                          conn['add'])
    
    if conn['return_object']:
      return object_data
    else:
      # we convert to dictionary
      return self.parse_object_from_firewall(object_data, object_info)
      
  def parse_object_from_firewall(self, object_data, object_info):
    object_list = []
    for obj in object_data:
      obj_info = self.get_object_attributes(obj, object_info['params'])
      if self.object_has_children(obj, object_info):
        obj_info['children'] = self.get_object_children(obj, object_info)
      object_list.append(dict(obj_info))

    return self.utils.return_sorted_list(object_list, 
                                         object_info['sort_param'])

  def get_object_attributes(self, obj, params):
    obj_info = {}
    for param in params:
      param_value = getattr(obj, param, None)
      if (param_value is not None or
          not self.utils.config['settings']['skip_null_param']):
        obj_info[param] = param_value
    return obj_info

  def object_has_children(self, obj, object_info):
    children = getattr(obj, 'children', False)
    if children and object_info.get('children', False):
      if len(children) > 0:
        return True
    return False

  def get_object_children(self, obj, object_info):
    children_dict = {}
    children = getattr(obj, 'children', [])
    for child_obj in children:
      for child_conf in object_info['children']:
        child_name = child_conf['name']
        child_conf_info = self.utils.api_params['children'][child_name]
        child_conf_class = self.utils.class_for_name(
          child_conf_info['module'],
          child_conf_info['class']
        )

        if isinstance(child_obj, child_conf_class):
          child_dict = self.get_object_attributes(
              child_obj, child_conf_info['params'])

          if self.object_has_children(child_obj, child_conf):
            child_dict['children'] = self.get_object_children(child_obj,
                                                              child_conf)
          
          if child_conf['name'] not in children_dict:
            children_dict[child_conf['name']] = []
          children_dict[child_conf['name']].append(child_dict)
          children_dict[child_conf['name']] = self.utils.return_sorted_list(
              children_dict[child_conf['name']], child_conf_info['sort_param'])

    return children_dict

  def set_api_keys(self, force=False, verify=False, hostname=None):
    # get credentials
    api_user, api_password = self.utils.ask_for_credentials(
        "Enter API username", "API password"
    )
    
    # iterate through hosts
    for host in self.utils.config['hosts']:
      if hostname is not None:
        if hostname != host['hostname']:
          continue

      self.utils.log.info(f"Setting API key for host: { host['hostname'] }")
      api_key = host.get('api_key', None)

      if force or api_key is None:
        self.set_api_key(host, api_user, api_password)
      else:
        # api_key already set
        if verify:
          try:
            fw = self.connect_to_fw(host['hostname'], 
                                    self.fix_api_key(api_key))
          except:
            self.set_api_key(host, api_user, api_password)
          else:
            continue
        else:
          continue

    # write config
    self.utils.write_config_file()

  def set_api_key(self, host_info, api_user, api_password):
    api_key = self.create_api_key(host_info['hostname'], api_user, 
                                  api_password)
    host_info['api_key'] = self.utils.encrypt(api_key)

  def create_api_key(self, hostname, api_user, api_password):
    if (api_user is None or api_password is None):
      return None
    
    query = {
      'type': 'keygen',
      'user': api_user,
      'password': api_password,
    }
    
    response = self.api_request(hostname, query)

    if response is not None:
      if response.status_code == 200:      
        return self.get_api_key_from_xml(response.text)
      else:
        return None
    else:
      return None

  def get_api_key_from_xml(self, xml):
    xml_root = etree.fromstring(xml)
    if xml_root is None:
      return xml_root
    
    xml_result = xml_root.find('result')
    if xml_result is None:
      return xml_result
    
    api_key = xml_result.find('key')
    if api_key is None:
      return None
    
    return api_key.text

  def api_request(self, hostname, query):
    url = f"https://{ hostname }/api/"
    return self.utils.url_post(url, query)

  def fix_api_key(self, api_key):
    if isinstance(api_key, bytes):
      return self.utils.decrypt(api_key)
    else:
      return api_key
