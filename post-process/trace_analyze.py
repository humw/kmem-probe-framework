#!/usr/bin/env python

import sys
import fileinput
import string
import re
import subprocess
from optparse import OptionParser
from os import path, walk
from collections import defaultdict

class Ptr:
	def __init__(self, fun, ptr, alloc, req):
		self.fun = fun
		self.ptr = ptr
		self.alloc = alloc
		self.req = req

class Callsite:
	def __init__(self, offset):
		self.offset = offset
		self.alloc = 0
		self.req = 0
		self.alloc_count = 0
		self.free_count = 0
		self.ptrs = []

	def curr_alloc(self):
		alloc = 0
		for ptr in self.ptrs:
			alloc += ptr.alloc
		return alloc
			
	def curr_req(self):
		req = 0
		for ptr in self.ptrs:
			req += ptr.req
		return req

class MemTreeNode:
	def __init__(self, name="", parent=None):
		self.name = name
		self.parent = parent
		self.childs = {}
		self.funcs = {}
		self.data = {}

	def fullName(self):
		l = [self.name,]
		parent = self.parent
		while parent:
			l.append(parent.name)
			parent = parent.parent

		return "/".join(reversed(l))

	def treelike(self, level=0):
		str = ""
		static_bytes = 0
		dynamic_bytes = 0
		for sym, size in self.data.items():
			static_bytes += size
		for sym, call in self.funcs.items():
			dynamic_bytes += call.curr_alloc()

#		if not self.childs and (static_bytes+dynamic_bytes) == 0:
#			return ""

		if self.name:
			str += "{} - static={} dyn={}\n".format(self.name, static_bytes, dynamic_bytes)

		for n, i in self.funcs.items():
			str += "{}{} - alloc={} req={}\n".format("  "*level, n, i.curr_alloc(), i.curr_req() )

#		for n, i in self.data.items():
#			str += "{}<D>{} - {}\n".format("  "*level, n, i )

		for name, node in self.childs.items():
			child_str = node.treelike(level+1)
			if child_str:
				str += "{}{}"	.format("  "*level, child_str)
		return str

	# Updates static, total current and total dynamic
	def fill(self):
		global f

		if self.funcs or self.data:
			print 'Oooops, already filled'

		filepath = "." + self.fullName() + "/built-in.o"
		p1 = subprocess.Popen(["readelf", "-s", filepath], stdout=subprocess.PIPE)
		output = p1.communicate()[0].split("\n")
		for line in output:
			if line == '':
				continue
			# strip trailing \n, if present
			if line[-1]=='\n':
				line = line[:-1]
			tmp = line
			m = re.match(r".*FUNC.*\b([a-zA-Z0-9_]+)\b", tmp)
			if m:
				if m.group(1) in self.funcs:
					print "Duplicate entry! {}".format(m.group(1))
	
				if m.group(1) in f:
					self.funcs[m.group(1)] = f[m.group(1)]

			m = re.match(r".*([0-9]+)\sOBJECT.*\b([a-zA-Z0-9_]+)\b", tmp)
			if m:
				self.data[m.group(2)] = int(m.group(1))


	# path is only dir, does not include built-in.o file
	def addChildPath(self, path):
		parts = path.split('/', 1)

		if len(parts) == 1:
			self.fill()
		else:
			node, others = parts
			if node not in self.childs:
				self.childs[node] = MemTreeNode(node, self)
			self.childs[node].addChildPath(others)

f = {}
p = {}

num_allocs = 0
num_frees = 0
num_lost_frees = 0
total_alloc = 0
total_req = 0

def add_kmalloc_event(fun, offset, ptr, req, alloc):

	global num_allocs, total_alloc, total_req
	num_allocs += 1
	total_alloc += alloc
	total_req += req

	ptr_obj = Ptr(fun, ptr, alloc, req)

	if ptr in p:
		print "Duplicate pointer! %s+0x%s, ptr=%s" % (fun, offset, ptr)

	p[ptr] = ptr_obj

	if not fun in f:
		f[fun] = Callsite(offset)

	f[fun].alloc += alloc
	f[fun].req += req
	f[fun].alloc_count += 1
	f[fun].ptrs.append(ptr_obj)

def add_kfree_event(fun, offset, ptr):

	global num_frees, num_lost_frees
	num_frees += 1

	if not ptr in p:
		num_lost_frees += 1
		return

	ptr_obj = p[ptr]
	f[ptr_obj.fun].free_count += 1

	# Remove this ptr from pointers list
	f[ptr_obj.fun].ptrs.remove(ptr_obj)
	# Remove it from pointers dictionary
	del p[ptr] 
	
def build_mem_tree(buildpath):

	print "Reading symbols for built kernel at {} ...".format(buildpath)

	tree = MemTreeNode()
	for root, dirs, files in walk(buildpath):
		for filepath in [path.join(root,f) for f in files]:
			if filepath.endswith("built-in.o"):
				tree.addChildPath(filepath)
	return tree

def print_statistics():

	global f,p
	curr_alloc = 0
	curr_req = 0
	for fun, callsite in f.items():
		curr_alloc += callsite.curr_alloc()
		curr_req += callsite.curr_req()

	print 'total bytes allocated: %8d' % total_alloc
	print 'total bytes requested: %8d' % total_req
	print 'total bytes wasted:    %8d' % (total_alloc - total_req)
	print 'curr bytes allocated:  %8d' % curr_alloc
	print 'curr bytes requested:  %8d' % curr_req
	print 'curr wasted bytes:     %8d' % (curr_alloc - curr_req)
	print 'number of allocs:      %8d' % num_allocs
	print 'number of frees:       %8d' % num_frees
	print 'number of lost frees:  %8d' % num_lost_frees
	print 'number of callers:     %8d' % len(f)

	print ''
	print '   total      req    waste alloc/free  caller'
	print '---------------------------------------------'
	for fun, callsite in f.items():
		print('%8d %8d %8d %5d/%-5d %s' % (callsite.alloc, 
			 			   callsite.req,
						   callsite.alloc - callsite.req,
						   callsite.alloc_count,
						   callsite.free_count,
						   fun))
	print ''
	print ' current      req    waste    ptrs     caller'
	print '---------------------------------------------'
	for fun, callsite in f.items():
		curr_alloc = callsite.curr_alloc()
		curr_req = callsite.curr_req()
		ptrs_count = len(callsite.ptrs)
		print('%8d %8d %8d %7d     %s' % (curr_alloc, 
						  curr_req,
						  curr_alloc - curr_req,
						  ptrs_count,
						  fun))
def main():

	parser = OptionParser()
	parser.add_option("-b", "--buildpath", dest="buildpath", default="linux",
        	          help="path to built kernel tree")

	(options, args) = parser.parse_args()

	print "Reading event log ..."

	for line in fileinput.input():
		# strip trailing \n, if present
		if line[-1]=='\n':
			line = line[:-1]

		# convert all hex numbers to symbols plus offsets
		# try to preserve column spacing in the output
		tmp = line
		m = re.match(r".*kmalloc.*call_site=([a-zA-Z0-9_]+)\+0x([a-f0-9]+).*ptr=([a-f0-9]+).*bytes_req=([0-9]+)\s*bytes_alloc=([0-9]+)", tmp)
		if m:
			add_kmalloc_event(m.group(1), 
					  m.group(2),
					  m.group(3),
					  int(m.group(4)),
					  int(m.group(5)))

		m = re.match(r".*kfree.*call_site=([a-zA-Z0-9_+.]+)\+0x([a-f0-9]+).*ptr=([a-f0-9]+)", tmp)
		if m:
			add_kfree_event(m.group(1),
					m.group(2),
					m.group(3))

	tree = build_mem_tree(options.buildpath)

	print(tree.treelike())

if __name__ == "__main__":
	main()
