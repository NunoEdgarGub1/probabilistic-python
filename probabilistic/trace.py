import inspect
import copy
from collections import Counter

class RandomVariableRecord:
	"""
	Variables generated by ERPs.
	These form the 'choice points' in a probabilistic program trace.
	"""

	def __init__(self, erp, params, val, logprob, structural, conditioned=False):
		self.erp = erp
		self.params = params
		self.val = val
		self.logprob = logprob
		self.active = True
		self.conditioned = conditioned
		self.structural = structural

class RandomExecutionTrace:
	"""
	Execution trace generated by a probabilistic program.
	Tracks the random choices made and accumulates probabilities
	"""

	def __init__(self, computation, doRejectionInit=True):
		self.computation = computation
		self._vars = {}
		self.logprob = 0
		self.newlogprob = 0		# From newly-added variables
		self.oldlogprob = 0		# From unreachable variables
		self.rootframe = None
		self.loopcounters = Counter()
		self.conditionsSatisfied = False
		self.returnValue = None
		if doRejectionInit:
			while not self.conditionsSatisfied:
				self._vars.clear()
				self.traceUpdate()

	def __deepcopy__(self, memo):
		newdb = RandomExecutionTrace(self.computation, doRejectionInit=False)
		newdb.logprob = self.logprob
		newdb.oldlogprob = self.oldlogprob
		newdb.newlogprob = self.newlogprob
		newdb._vars = {name:copy.copy(record) for name,record in self._vars.iteritems()}
		newdb.conditionsSatisfied = self.conditionsSatisfied
		newdb.returnValue = self.returnValue
		return newdb

	def freeVarNames(self, structural=True, nonstructural=True):
		return map(lambda tup: tup[0], \
				   filter(lambda tup: not tup[1].conditioned and \
				   					  ((structural and tup[1].structural) or (nonstructural and not tup[1].structural)), \
						  self._vars.iteritems()))

	def varDiff(self, other):
		"""
		The names of the variables that this trace has that the other trace does not have
		"""
		return list(set(self._vars.keys()) - set(other._vars.keys()))

	def lpDiff(self, other):
		"""
		The difference in log probability between this trace and the other resulting
		from the variables that this has that the other does not
		"""
		return sum(map(lambda name: self._vars[name].logprob, self.varDiff(other)))

	def traceUpdate(self):
		"""
		Run computation and update this trace accordingly
		"""

		global _trace
		originalTrace = _trace
		_trace = self

		self.logprob = 0.0
		self.newlogprob = 0.0
		self.loopcounters.clear()
		self.conditionsSatisfied = True

		# First, mark all random values as 'inactive'; only
		# those reeached by the computation will become 'active'
		for record in self._vars.values():
			record.active = False

		# Mark that this is the 'root' of the current execution trace
		self.rootframe = inspect.currentframe()

		# Run the computation, which will create/lookup random variables
		self.returnValue = self.computation()

		# CLear out the root frame, etc.
		self.rootframe = None
		self.loopcounters.clear()

		# Clean up any random values that are no longer reachable
		self.oldlogprob = 0.0
		for record in self._vars.values():
			if not record.active:
				self.oldlogprob += record.logprob
		self._vars = {name:record for name,record in self._vars.iteritems() if record.active}

		_trace = originalTrace

	def proposeChange(self, varname):
		"""
		Propose a random change to the variable name 'varname'
		Returns a new sample trace from the computation and the
			forward and reverse probabilities of proposing this change
		"""
		nextTrace = copy.deepcopy(self)
		var = nextTrace.getRecord(varname)
		propval = var.erp._proposal(var.val, var.params)
		fwdPropLP = var.erp._logProposalProb(var.val, propval, var.params)
		rvsPropLP = var.erp._logProposalProb(propval, var.val, var.params)
		var.val = propval
		var.logprob = var.erp._logprob(var.val, var.params)
		nextTrace.traceUpdate()
		fwdPropLP += nextTrace.newlogprob
		rvsPropLP += nextTrace.oldlogprob
		return nextTrace, fwdPropLP, rvsPropLP

	def currentName(self, numFrameSkip):
		"""
		Return the current name, as determined by the interpreter
			stack of the current program.
		Skips the top 'numFrameSkip' stack frames that precede this
			function's stack frame (numFrameSkip+1 frames total)
		"""
		numFrameSkip += 1	# Skip this frame, obviously
		f = inspect.currentframe()
		for i in xrange(numFrameSkip):
			f = f.f_back
		name = ""
		while f and f is not self.rootframe:
			name = "{0}:{1}:{2}:{3}|".format(id(f.f_code), self.loopcounters[id(f)], f.f_lineno, f.f_lasti) + name
			f = f.f_back
		return name

	def incrementLoopCounter(self, numFrameSkip):
		"""
		Increment the loop counter associated with the frame that is numFrameSkip
		frames from the top of the stack
		"""
		numFrameSkip += 1	# Skip this frame, obviously
		f = inspect.currentframe()
		for i in xrange(numFrameSkip):
			f = f.f_back
		self.loopcounters[id(f)] += 1

	def lookup(self, name, erp, params, isStructural, conditionedValue=None):
		"""
		Looks up the value of a random variable.
		If this random variable does not exist, create it
		"""

		record = self._vars.get(name)
		if (not record or record.erp is not erp or
			isStructural != record.structural or
			(conditionedValue and conditionedValue != record.val)):
			# Create new variable
			val = (conditionedValue if conditionedValue else erp._sample_impl(params))
			ll = erp._logprob(val, params)
			self.newlogprob += ll
			record = RandomVariableRecord(erp, params, val, ll, isStructural, conditionedValue != None)
			self._vars[name] = record
		else:
			# Reuse existing variable
			if record.params != params:
				record.params = params
				record.logprob = erp._logprob(record.val, params)
		self.logprob += record.logprob
		record.active = True
		return record.val

	def getRecord(self, name):
		"""
		Simply retrieve the variable record associated with name
		"""
		return self._vars.get(name)

	def addFactor(self, num):
		"""
		Add a new factor into the log likelihood of the current trace
		"""
		self.logprob += num

	def conditionOn(self, boolexpr):
		"""
		Condition the trace on the value of a boolean expression
		"""
		self.conditionsSatisfied = self.conditionsSatisfied and boolexpr

"""
Global singleton instance
"""
_trace = None

def lookupVariableValue(erp, params, isStructural, numFrameSkip, conditionedValue=None):
	global _trace
	if not _trace:
		return (conditionedValue if conditionedValue else erp._sample_impl(params))
	else:
		name = _trace.currentName(numFrameSkip+1)
		return _trace.lookup(name, erp, params, isStructural, conditionedValue)

def incrementLoopCounter(numFrameSkip):
	global _trace
	if _trace:
		_trace.incrementLoopCounter(numFrameSkip+1)

def newTrace(computation):
	return RandomExecutionTrace(computation)

def factor(num):
	global _trace
	if _trace:
		_trace.addFactor(num)

def condition(boolexpr):
	global _trace
	if _trace:
		_trace.conditionOn(boolexpr)