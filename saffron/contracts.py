import re
import os
import json
import pprint
import random
import logging
import argparse
import subprocess
from io import BytesIO

from web3 import Web3, HTTPProvider
from web3.eth import Eth, Contract
from solc import compile_source, compile_standard
from jinja2 import Environment
from jinja2.nodes import Name

from saffron.accounts import Account
from saffron.genesis import Chain
from saffron import database

import sqlite3

import pickle

log = logging.getLogger(__file__)

DEFAULT_CONTRACT_DIRECTORY = './contracts'

insert_contract_sql = '''
			INSERT INTO contracts (
			name,
			abi,
			metadata,
			gas_estimates,
			method_identifiers) VALUES (?,?,?,?,?)'''

update_contracts_sql = '''
			UPDATE contracts
			SET
			address = ?,
			instance = ?,
			deployed = 'true'
			where name = ? ;'''

input_json = '''{"language": "Solidity", "sources": {
				"{{name}}": {
					"content": {{sol}}
				}
			},
			"settings": {
				"outputSelection": {
					"*": {
						"*": [ "metadata", "evm.bytecode", "abi", "evm.bytecode.opcodes", "evm.gasEstimates", "evm.methodIdentifiers" ]
					}
				}
			}
		}'''

def insert_contract(name, abi, bytecode, gas_estimates, method_identifiers, cwd=False):
	#pickle the blobs and add them to the db
	gas = pickle.dumps(gas_estimates)
	methods = pickle.dumps(method_identifiers)
	return Chain(cwd=cwd).database.cursor.execute(insert_contract_sql, (name,
					str(abi),
					bytecode,
					sqlite3.Binary(gas),
					sqlite3.Binary(methods)))

def update_contract(address, instance, name):
	Chain().database.cursor.execute(update_contracts_sql, (address, pickle.dumps(instance), name))

def get_template_variables(fo):
	nodes = Environment().parse(fo.read()).body[0].nodes
	var_names = [x.name for x in nodes if type(x) is Name]
	return var_names

def render_contract(payload, contract_directory=DEFAULT_CONTRACT_DIRECTORY):
	sol_contract = payload.pop('sol')
	template_variables = get_template_variables(BytesIO(sol_contract.encode()))
	assert 'contract_name' in payload
	name = payload.get('contract_name')
	assert all(x in template_variables for x in list(payload.keys()))
	template = Environment().from_string(sol_contract)
	return name, template.render(payload)

def load_tsol_file(file=None, payload=None):
	assert file and payload, 'No file or payload provided.'
	payload['sol'] = file.read()
	name, rendered_contract = render_contract(payload=payload)
	return name, rendered_contract

def name_is_unique(name):
	_name, _address = database.contract_exists(name=name)
	if _name is None and _address is None:
		return True
	return False

def load_sol_file(file=None):
	assert file, 'No file provided'
	return file.read()

# class Manager(object):
# 	def request_blocking(self, *args):
# 		print(args)
# 		return '0x0000000000000000000000000000000000000000'

# # deploy contract
# class AB(object):
# 	pass
# 	estimateGas = lambda self, x: 9
# 	blockNumber = print
# 	manager = print
# 	getBlock = lambda self, x: {'gasLimit': 10000000, 'x': x}
# 	def __init__(self):
# 		self.eth = self
# 		self.web3 = self
# 		self.eth.web3.manager = Manager()
# 		self.manager = self.manager
# 		#import pdb;pdb.set_trace()

# A = AB()


class Contract(Contract):
	def __init__(self, name, sol_file_path):
		assert name != None, 'A name identifier must be provided to create a new contract instance.'
		_name, _address = database.contract_exists(name=name)
		assert _name is None and _address is None
		self.web3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8000"))
		self.name = name
		self.is_deployed = None
		with open(sol_file_path) as f:
			self.sol = load_sol_file(f)
		self.template_json = Environment().from_string(input_json).render(name=self.name, sol=json.dumps(self.sol))
		self.output_json = compile_standard(json.loads(self.template_json))
		self.compiled_name = list(self.output_json['contracts'][self.name].keys())[0]
		self.contracts = self.output_json['contracts'][self.name][self.compiled_name]
		self.abi = self.contracts['abi']
		self.metadata = self.contracts['metadata']
		self.bytecode = self.contracts['evm']['deployedBytecode']['object']
		self.gas_estimates = self.contracts['evm']['gasEstimates']
		self.method_identifiers = self.contracts['evm']['methodIdentifiers']

		# set in deploy
		self.address = None
		self.instance = None
		self.defaulAccount = '0xabaa886e5c11c54e76d250efd70143fe0f959530'
		# self.web3.personal.unlockAccount(self.defaulAccount, 'password');

	def __str__(self):
		return 'Contract {}, {}'.format(self.address if self.address else 'None', self.name if self.name else 'None')

	def from_chain(self):
		raise NotImplementedError('TODO')

	def deploy(self, cwd=False):
		assert not self.is_deployed, 'This contract already exists on the chain.'
		assert self.sol, 'No solidity code loaded into this object'

		response = insert_contract(self.name,
								self.abi,
								self.bytecode,
								self.gas_estimates,
								self.method_identifiers,
								cwd)

		# ok = web3.eth.Eth(web3)
		self.address = self.web3.eth.sendTransaction(transaction={'data' : '0x' + self.bytecode, 'from': self.defaulAccount, 'gaslimit': 30000})
		self.instance = self.web3.eth.contract(self.address)
		#update the deployed and address to the db and an instance for pulling and interacting with the contract again
		contract_instance = update_contract(json.dumps(self.address), self.method_identifiers, self.name)


