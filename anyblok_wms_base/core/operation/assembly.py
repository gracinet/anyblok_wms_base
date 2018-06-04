# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.
import itertools

from anyblok import Declarations
from anyblok.column import Integer
from anyblok.column import Text
from anyblok_postgres.column import Jsonb
from anyblok.relationship import Many2One

from anyblok_wms_base.exceptions import (OperationInputsError,
                                         AssemblyInputNotMatched,
                                         AssemblyExtraInputs,
                                         AssemblyPropertyConflict,
                                         AssemblyWrongInputProperties,
                                         UnknownExpressionType,
                                         OperationError,
                                         )
from anyblok_wms_base.constants import (DEFAULT_ASSEMBLY_NAME,
                                        CONTENTS_PROPERTY,
                                        )

register = Declarations.register
Mixin = Declarations.Mixin
Operation = Declarations.Model.Wms.Operation

_missing = object()
"""A marker to use as default value in get-like functions/methods."""


OPERATION_STATES = ('planned', 'started', 'done')


def state_interval(from_state, to_state):
    """Return a tuple of states to pass in a state jump.

    :param from_state: the state to start from, or None
    :param to_state: the state to reach
    """
    if from_state is None:
        from_state_idx = 0
    else:
        from_state_idx = OPERATION_STATES.index(from_state) + 1
    return OPERATION_STATES[from_state_idx:
                            OPERATION_STATES.index(to_state) + 1]


class CheckMatch:
    """Used to store check and match property requirements directive.

    The added value of this class is to make the merging of parameters easier
    for 'requirements' (whose value is 'check' or 'match'), with the same API
    as :class:`dict` and :class:`set` implementing the rule that 'match'
    wins over 'check'.
    """

    is_match = False

    def update(self, upd):
        if upd not in ('check', 'match'):
            raise ValueError(upd)
        self.is_match = self.is_match or upd == 'match'


def merge_state_parameter(spec, from_state, to_state, param_type):
    """Utility method to merge sets or dict parameter for state jumps.

    :param spec: a dict whose keys are Operation states and values are the
                 per-state parameter values
    :param from_state: the state to start from, or None
    :param to_state: the state to reach
    :param str param_type:
        the type of the parameter (can be ``'set'`` or ``'dict'``)
    :return: merged value for the parameter
    :raises: ValueError for unknown types

    Besides doing the aggregation, this normalizes things a bit.
    For instance, ``spec`` can be ``None``, and in that case,  we'll get
    an appropriate empty value.
    """
    if param_type == 'set':
        res = set()
    elif param_type == 'dict':
        res = {}
    elif param_type == 'check_match':
        res = CheckMatch()
    else:
        raise ValueError(
            "Unsupported parameter type for state merging: %r" % param_type)

    if spec is None:
        return res

    for step in state_interval(from_state, to_state):
        step_param = spec.get(step)
        if step_param is None:
            continue
        res.update(step_param)
    return res


def merge_state_sub_parameters(spec, from_state, to_state, *subkeys):
    """Utility method to merge sets or dict parameters for state jumps.

    :param spec: a dict whose keys are Operation states
    :param from_state: the state to start from, or None
    :param to_state: the state to reach
    :param subkeys: each one is a pair (subkey, type) where subkey is the
                    key to consider inside each state subdict of ``spec``
                    and type is a string representing the type of the
                    corresponding values (``'set'`` or ``'dict'``).
    :return: values for the subkeys, in lexical order if there are several
             or just the value if there's one.
    :raises: ValueError for unknown types

    Besides doing the aggregation, this normalizes things a bit.
    For instance, ``spec`` can be ``None``, and in that case,  we'll get
    empty values for the subkeys.
    """
    res = []
    for subkey, subtype in subkeys:
        if subtype == 'set':
            res.append(set())
        elif subtype == 'dict':
            res.append({})
        else:
            raise ValueError(
                "Unknown subkey type %r for subkey %r" % (subtype, subkey))

    if spec is None:
        return None if not res else res if len(res) > 1 else res[0]

    for step in state_interval(from_state, to_state):
        step_spec = spec.get(step)
        if step_spec is None:
            continue
        for i, (k, _) in enumerate(subkeys):
            res[i].update(step_spec.get(k, ()))

    return None if not res else res if len(res) > 1 else res[0]


@register(Operation)
class Assembly(Operation):
    """Assembly/Pack Operation.

    This operation covers simple packing and assembly needs : those for which
    a single outcome is produced from the inputs, which must be in the same
    Location. More general manufacturing cases fall out of the scope of
    the ``wms-core`` Blok.

    The behaviour is specified on the :attr:`outcome's Goods Type
    <outcome_type>`, and amounts to describing the expected inputs,
    and how to build the Properties of the outcome (see
    :meth:`build_outcome_properties`)

    A given Type can be assembled in different ways (TODO use-cases even
    for simple packing), and this gets specified by the :attr:`name` field.

    Besides being the main key for the
    :attr:`Assembly specification <specification>`,
    the :attr:`name` is also used to dispatch hooks for specific logic that
    would be too complicated to describe in configuration (see
    :meth:`specific_build_outcome_properties`).
    """
    TYPE = 'wms_assembly'

    id = Integer(label="Identifier",
                 primary_key=True,
                 autoincrement=False,
                 foreign_key=Operation.use('id').options(ondelete='cascade'))

    outcome_type = Many2One(model='Model.Wms.Goods.Type', nullable=False)
    """The :class:`Goods Type
    <anyblok_wms_base.core.goods.Type>` to produce.
    """

    name = Text(nullable=False, default=DEFAULT_ASSEMBLY_NAME)
    """The name of the assembly, to be looked up in behaviour.

    This field has a default value to accomodate the common case where there's
    only one assembly for the given :attr:`outcome_type`.

    .. note:: the default value is not enforced before flush, this can
              prove out to be really inconvenient for downstream code.
              TODO apply the default value in :meth:`check_create_conditions`
              for convenience ?
    """

    match = Jsonb()
    """Field use to store the result of inputs matching

    Assembly Operations match their actual inputs (set at creation)
    with the ``inputs`` part of :attr:`specification`.
    This field is used to store the
    result, so that it's available for further logic (for instance in
    the :meth:`property setting hooks
    <specific_build_outcome_properties>`).

    This field's value is either ``None`` (before matching) or a list
    of lists: for each of the inputs specification, respecting
    ordering, the list of ids of the matching Avatars.
    """

    @property
    def extra_inputs(self):
        matched = set(av_id for m in self.match for av_id in m)
        return (av for av in self.inputs if av.id not in matched)

    def specific_repr(self):
        return ("outcome_type={self.outcome_type!r}, "
                "name={self.name!r}").format(self=self)

    @classmethod
    def check_create_conditions(cls, state, dt_execution,
                                inputs=None, outcome_type=None, name=None,
                                **kwargs):
        super(Assembly, cls).check_create_conditions(
            state, dt_execution, inputs=inputs,
            **kwargs)
        behaviour = outcome_type.behaviours.get('assembly')
        if behaviour is None:
            raise OperationError(
                cls, "No assembly specified for type {outcome_type!r}",
                outcome_type=outcome_type)
        spec = behaviour.get(name)
        if spec is None:
            raise OperationError(
                cls,
                "No such assembly: {name!r} for type {outcome_type!r}",
                name=name, outcome_type=outcome_type)

        loc = inputs[0].location
        if any(inp.location != loc for inp in inputs[1:]):
            raise OperationInputsError(
                cls,
                "Inputs {inputs} are in different Locations: {locations!r}",
                inputs=inputs,
                # in the passing case, building a set would have been
                # useless overhead
                locations=set(inp.location for inp in inputs))

    def extract_property(self, extracted, goods, prop,
                         exc_details=None):
        """Extract the wished property from goods, forbidding conflicts.

        :param str prop: Property name
        :param dict extracted:
           the specified property value is read from `goods` and stored there,
           if not already present with a different value
        :param exc_details: If specified the index and value of the input
                            specifification this comes from, for exception
                            raising (the exception will assume that the
                            conflict arises in the global forward_properties
                            directive).
        :raises: AssemblyPropertyConflict
        """
        candidate_value = goods.get_property(prop, default=_missing)
        if candidate_value is _missing:
            return
        try:
            existing = extracted[prop]
        except KeyError:
            extracted[prop] = candidate_value
        else:
            if existing != candidate_value:
                raise AssemblyPropertyConflict(self, exc_details, prop,
                                               existing, candidate_value)

    def forward_properties(self, state, for_creation=False):
        """Forward properties from the inputs to the outcome

        This is done according to the global specification

        :param state: the Assembly state that we are reaching.
        :param bool for_creation: if ``True``, means that this is part
                                  of the creation process, i.e, there's no
                                  previous state.
        :raises: AssemblyPropertyConflict if forwarding properties
                 changes an already set value.
        """
        spec = self.specification
        Avatar = self.registry.Wms.Goods.Avatar
        global_spec = spec.get('inputs_properties')
        glob_fwd = merge_state_sub_parameters(
            global_spec,
            None if for_creation else self.state,
            state,
            ('forward', 'set')
            )

        inputs_spec = spec.get('inputs', ())

        forwarded = {}

        for i, (match_item, input_spec) in enumerate(
                zip(self.match, inputs_spec)):
            fwd = input_spec.get('forward_properties', ())
            for av_id in match_item:
                goods = Avatar.query().get(av_id).goods
                for fp in itertools.chain(fwd, glob_fwd):
                    self.extract_property(forwarded, goods, fp,
                                          exc_details=(i, input_spec))
        for extra in self.extra_inputs:
            for fp in glob_fwd:
                self.extract_property(forwarded, extra.goods, fp)

        return forwarded

    def check_inputs_properties(self, state, for_creation=False):
        """Apply global and per input Property requirements according to state.

        All property requirements between the current state (or None if we
        are at creation) and the wished state are checked.

        :param state: the state that the Assembly is about to reach
        :param for_creation: if True, the current value of the :attr:`state`
                             field is ignored, and all states up to the wished
                             state are considered.
        :raises: :class:`AssemblyWrongInputProperties`
        """
        spec = self.specification
        global_props_spec = spec.get('inputs_properties')
        if global_props_spec is None:
            return

        req_props, req_prop_values = merge_state_sub_parameters(
            global_props_spec,
            None if for_creation else self.state,
            state,
            ('required', 'set'),
            ('required_values', 'dict'),
        )

        for avatar in self.inputs:
            goods = avatar.goods
            if (not goods.has_properties(req_props) or
                    not goods.has_property_values(req_prop_values)):
                raise AssemblyWrongInputProperties(
                    self, avatar, req_props, req_prop_values)

        Avatar = self.registry.Wms.Goods.Avatar
        for i, (match_item, input_spec) in enumerate(
                zip(self.match, spec.get('inputs', ()))):
            req_props, req_prop_values = merge_state_sub_parameters(
                input_spec.get('properties'),
                None if for_creation else self.state,
                state,
                ('required', 'set'),
                ('required_values', 'dict'),
            )
            for av_id in match_item:
                goods = Avatar.query().get(av_id).goods
                if (not goods.has_properties(req_props) or
                        not goods.has_property_values(req_prop_values)):
                    raise AssemblyWrongInputProperties(
                        self, avatar, req_props, req_prop_values,
                        spec_item=(i, input_spec))

    def match_inputs(self, state, for_creation=False):
        """Compare input Avatars to specification and apply Properties rules.

        :param state: the state for which to perform the matching
        :return: extra_inputs, an iterable of
                 inputs that are left once all input specifications are met.
        :raises: :class:`anyblok_wms_base.exceptions.AssemblyInputNotMatched`,
                 :class:`anyblok_wms_base.exceptions.AssemblyForbiddenExtraInputs`

        """
        # let' stress that the incoming ordering shouldn't matter
        # from this method's point of view. And indeed, only in tests can
        # it come from the will of a caller. In reality, it'll be due to
        # factors that are random wrt the specification.
        inputs = set(self.inputs)
        spec = self.specification

        GoodsType = self.registry.Wms.Goods.Type
        types_by_code = dict()
        from_state = None if for_creation else self.state

        match = self.match = []

        for i, expected in enumerate(spec['inputs']):
            match_item = []
            match.append(match_item)

            req_props, req_prop_values = merge_state_sub_parameters(
                expected.get('properties'),
                from_state,
                state,
                ('required', 'set'),
                ('required_values', 'dict'),
            )

            type_code = expected['type']
            gtype = types_by_code.get(type_code)
            if gtype is None:
                gtype = GoodsType.query().filter_by(
                    code=type_code).one()
                types_by_code[type_code] = gtype
            for _ in range(expected['quantity']):
                for candidate in inputs:
                    goods = candidate.goods
                    if (not goods.has_type(gtype) or
                            not goods.has_properties(req_props) or
                            not goods.has_property_values(req_prop_values)):
                        continue
                    inputs.discard(candidate)
                    match_item.append(candidate.id)
                    break
                else:
                    raise AssemblyInputNotMatched(self, (expected, i),
                                                  from_state=from_state,
                                                  to_state=state)

        if inputs and not spec.get('allow_extra_inputs'):
            raise AssemblyExtraInputs(self, inputs)
        return inputs

    @property
    def specification(self):
        """The Assembly specification

        The Assembly specification is read from the ``assembly`` part of
        the behaviour field of :attr:`outcome_type`. Namely, it is, within
        that part, the value associated with :attr:`name`.

        Here's an example, for an Assembly whose :attr:`name` is
        ``'soldering'``, also displaying all standard parameters::

          behaviours = {
             …
             'assembly': {
                 'soldering': {
                     'outcome_properties': {
                         'planned': {'built_here': ['const', True]},
                         'started': {'spam': ['const', 'eggs']},
                         'done': {'serial': ['sequence', 'SOLDERINGS']},
                     },
                     'inputs': [
                         {'type': 'GT1',
                          'quantity': 1,
                          'properties': {
                             'planned': {
                               'required': ['x'],
                             },
                             'started': {
                               'required': ['foo'],
                               'required_values': {'x': True},
                               'requirements': 'match',  # default is 'check'
                             },
                             'done': {
                               'forward': ['foo', 'bar'],
                               'requirements': 'check',
                             }
                          },
                         {'type': 'GT2',
                          'quantity': 2
                          },
                         {'type': 'GT3',
                          'quantity': 1,
                          'code': 'ABC',
                          }
                     ],
                     'inputs_spec_type': {
                         'planned': 'check',  # default is 'match'
                         'started': 'match',  # default is 'check' for
                                              # 'started' and 'done' states
                      },
                     'for_contents': ['all', 'descriptions'],
                     'allow_extra': True,
                     'inputs_properties': {
                         'planned': {
                            'required': …
                            'required_values': …
                            'forward': …
                         },
                         'started': …
                         'done': …
                     }
                 }
                 …
              }
          }

        .. note:: Non standard parameters can be specified, for use in
                  :meth:`Specific hooks <specific_build_outcome_properties>`.

        The present Python property performs no checks,
        since it is meant to be accessed only after the protection of
        :meth:`check_create_conditions`.
        """
        return self.outcome_type.behaviours['assembly'][self.name]

    DEFAULT_FOR_CONTENTS = ('extra', 'records')
    """Default value of the ``for_contents`` part of specification.

    See :meth:`build_outcome_properties` for the meaning of the values.
    """

    def build_outcome_properties(self, state, for_creation=False):
        """Method responsible for initial properties on the outcome.

        :param state: The Assembly state that we are reaching.
        :param bool for_creation: if ``True``, means that this is part
                                  of the creation process, i.e, there's no
                                  previous state.
        :rtype: :class:`Model.Wms.Goods.Properties
                <anyblok_wms_base.core.goods.Properties>`
        :raises: :class:`AssemblyInputNotMatched` if one of the
                 :attr:`input specifications <specification>` is not
                 matched by ``self.inputs``,
                 :class:`AssemblyPropertyConflict` in case of conflicting
                 values for the outcome.

        **Property specifications**

        The Assembly :attr:`specification` can have the following
        key/value pairs:

        * ``outcome_properties``:
             a dict whose keys are Assembly states, and values are
             dicts of Properties to set on the outcome; the values
             are pairs ``(TYPE, EXPRESSION)``, evaluated by passing as
             positional arguments to :meth:`eval_typed_expr`.
        * ``inputs_properties``:
             a dict whose keys are Assembly states, and values are themselves
             dicts with key/values:

             + required:
                 list of properties that must be present on all inputs
                 while reaching the given Assembly state, whatever their
                 values
             + required_values:
                 dict of Property key/value pairs that all inputs must bear
                 while reaching the given Assembly state.
             + forward:
                 list of properties to forward to the outcome while
                 reaching the given Assembly state.

        **Per input specification matching and forwarding**

        The ``inputs_properties`` parameters can also be specified
        inside each :class:`dict` that form
        the ``inputs`` list of the :meth:`Assembly specification <spec>`),
        as the ``properties`` sub parameter.

        In that case, the Property requirements are used either as
        matching criteria on the inputs, or as a check on already matched
        Goods, according to the value of the ``inputs_spec_type`` parameter
        (default is ``'match'`` in the ``planned`` Assembly state,
        and ``'check'`` in the other states).

        Example::

          'inputs_spec_type': {
              'started': 'match',  # default is 'check' for
                                   # 'started' and 'done' states
          },
          'inputs': [
              {'type': 'GT1',
               'quantity': 1,
               'properties': {
                   'planned': {'required': ['x']},
                   'started': {
                       'required_values': {'x': True},
                   },
                   'done': {
                       'forward': ['foo', 'bar'],
                   },
              …
          ]

        During matching, per input specifications are applied in order,
        but remember that
        the ordering of ``self.inputs`` itself is to be considered random.

        In case ``inputs_spec_type`` is ``'check'``, the checking is done
        on the Goods matched by previous states, thus avoiding a potentially
        costly rematching. In the above example, matching will be performed
        in the ``'planned'`` and ``'started'`` states, but a simple check
        will be done if going from the ``started`` to the ``done`` state.

        It is therefore possible to plan an Assembly with partial information
        about its inputs (waiting for some Observation, or a previous Assembly
        to be done), and to
        refine that information, which can be displayed to operators, or have
        consequences on the Properties of the outcome, at each state change.
        In many cases, rematching the inputs for all state changes is
        unnecessary. That's why, to avoid paying the computational cost
        three times, the default value is ``'check'`` for the ``done`` and
        ``started`` states.

        The result of matching is stored in the :attr:`match` field.

        In all cases, if a given Property is to be forwarded from several
        inputs to the outcome and its values on these inputs aren't equal,
        :class:`AssemblyPropertyConflict` will be raised.

        **Bypassing states**

        Following the general expectations about states of Operations, if
        an Assembly is created directly in the ``done`` state, it will apply
        the ``outcome_properties`` for the ``planned``, ``started`` and
        ``done`` states.
        Also, the matching and checks of input Properties for the ``planned``,
        ``started`` and ``done`` state will be performed, in that order.

        In other words, it behaves exactly as if it had been first planned,
        then started, and finally executed.

        Similarly, if a planned Assembly is executed (without being started
        first), then outcome Properties, matches and checks related to the
        ``started`` state are performed before those of the ``done`` state.

        **Specific hooks**

        While already powerful, the Property manipulations described above
        are not expected to fit all situations, especially the rule about
        differing values on inputs. On the other hand, trying to accomodate
        all use cases through configuration would lead to insanity.

        Therefore, the core will stick to these still
        relatively simple primitives, but will also provide the means
        to perform custom logic, through :meth:`assembly-specific hooks
        <specific_build_outcome_properties>`

        Namely, :meth:`specific_build_outcome_properties` gets called near
        the end of the process. and the built Properties are built according
        to its result, with higher precedence than any other source of
        properties.


        **The contents Property**

        The outcome also bears the special :data:`contents property
        <anyblok_wms_base.constants.CONTENTS_PROPERTY>` (
        used by :class:`Operation.Unpack
        <anyblok_wms_base.core.operation.unpack.Unpack>`).

        This is controlled by the
        ``for_contents`` part of the assembly specification, which
        itself is a pair, whose first element indicates which inputs to list,
        and the second how to list them. Its default value is
        :attr:`DEFAULT_FOR_CONTENTS`. It can also be explicitely set to
        ``None``, to tell the Assembly not to set the contents property
        (use-cases: if it's unnecessary pollution, for instance if it
        is later custom set by specific hooks, or if no Unpack for disassembly
        is ever to be wished anyway).

        *for_contents: possible values of first element:*

        * ``'all'``:
             all inputs will be listed
        * ``'extra'``:
            only the actual inputs that aren't specified in the
            behaviour will be listed. This is useful in cases where
            the Unpack behaviour already takes the specified ones into
            account. Hence, the variable parts of Assembly and Unpack are
            consistent.

        *for_contents: possible values of second element:*

        * ``'descriptions'``:
            include Goods' Types, those Properties that aren't recoverable by
            an Unpack from the Assembly outcome, together with appropriate
            ``forward_properties`` for those who are (TODO except those that
            come from a global ``forward`` in the Assembly specification)
        * ``'records'``:
            same as ``descriptions``, but also includes the record ids, so
            that an Unpack following the Assembly would not give rise to new
            Goods records, but would reuse the existing ones, hence keep the
            promise that the Goods records are meant to track the "sameness"
            of the physical objects.
        """
        spec = self.specification
        assembled_props = self.forward_properties(state,
                                                  for_creation=for_creation)

        contents = self.build_contents(assembled_props)
        if contents:
            assembled_props[CONTENTS_PROPERTY] = contents

        prop_exprs = merge_state_parameter(
            spec.get('outcome_properties'),
            None if for_creation else self.state,
            state,
            'dict')
        assembled_props.update((k, self.eval_typed_expr(*v))
                               for k, v in prop_exprs.items())

        assembled_props.update(self.specific_build_outcome_properties(
            assembled_props, state, for_creation=for_creation))
        return self.registry.Wms.Goods.Properties.create(**assembled_props)

    props_hook_fmt = "build_outcome_properties_{name}"

    def specific_build_outcome_properties(self, assembled_props, state,
                                          for_creation=False):
        """Hook for per-name specific update of Properties on outcome.

        At the time of Operation creation or execution,
        this calls a specific method whose name is derived from the
        :attr:`name` field, :attr:`by this format <props_hook_fmt>`, if that
        method exists.

        Applicative code is meant to override the present Model to provide
        the specific method. The signature to implement is identical to the
        present one:

        :param dict assembled_props:
           a :class:`dict` of already built Properties, or a
           :class:`Properties
           <anyblok_wms_base.core.goods.Properties>` instance.
        :param state: The Assembly state that we are reaching.
        :param bool for_creation:
            if ``True``, means that this is part of the creation process,
            i.e, there's no previous state.
        :return: the properties to set or update
        :rtype: any iterable that can be passed to :meth:`dict.update`.

        """
        meth = getattr(self, self.props_hook_fmt.format(name=self.name), None)
        if meth is None:
            return ()
        return meth(assembled_props, state, for_creation=for_creation)

    def build_contents(self, forwarded_props):
        """Construction of the ``contents`` property

        This is part of :meth`build_outcome_properties`
        """
        contents_spec = self.specification.get('for_contents',
                                               self.DEFAULT_FOR_CONTENTS)
        if contents_spec is None:
            return
        what, how = contents_spec
        if what == 'extra':
            for_unpack = self.extra_inputs
        elif what == 'all':
            for_unpack = self.inputs
        contents = []

        # sorting here and later is for tests reproducibility
        for avatar in sorted(for_unpack, key=lambda av: av.id):
            goods = avatar.goods
            props = goods.properties
            unpack_outcome = dict(
                type=goods.type.code,
                quantity=1,  # TODO hook for wms_quantity
                )
            if props is not None:
                unpack_outcome_fwd = []
                for k, v in props.as_dict().items():
                    if k in forwarded_props:
                        unpack_outcome_fwd.append(k)
                    else:
                        unpack_outcome.setdefault('properties', {})[k] = v
                unpack_outcome_fwd.sort()
                if unpack_outcome_fwd:
                    unpack_outcome['forward_properties'] = unpack_outcome_fwd

            contents.append(unpack_outcome)
            if how == 'records':
                # Adding local goods id so that a forthcoming unpack
                # would produce the very same goods.
                # TODO this *must* be discarded in case of Departures with
                # EDI,  and maybe some other ones. How to do that cleanly and
                # efficiently ?
                unpack_outcome['local_goods_ids'] = [goods.id]

        return contents

    def check_match_inputs(self, to_state, for_creation=False):
        """Check or match inputs according to specification.

        :rtype bool:
        :return: ``True`` iff a match has been performed
        """
        spec = self.specification.get('inputs_spec_type')
        if spec is None:
            spec = {}
        spec.setdefault('planned', 'match')

        cm = merge_state_parameter(spec,
                                   None if for_creation else self.state,
                                   to_state,
                                   'check_match')
        (self.match_inputs if cm.is_match else self.check_inputs_properties)(
            to_state, for_creation=for_creation)
        return cm.is_match

    def after_insert(self):
        state = self.state
        outcome_state = 'present' if state == 'done' else 'future'
        dt_exec = self.dt_execution
        input_upd = dict(dt_until=dt_exec)
        if state == 'done':
            input_upd.update(state='past', reason=self)
        # TODO PERF bulk update ?
        for inp in self.inputs:
            inp.update(**input_upd)

        self.check_match_inputs(state, for_creation=True)
        Goods = self.registry.Wms.Goods
        Goods.Avatar.insert(
            goods=Goods.insert(
                type=self.outcome_type,
                properties=self.build_outcome_properties(state,
                                                         for_creation=True)),
            location=self.inputs[0].location,
            reason=self,
            state=outcome_state,
            dt_from=dt_exec,
            dt_until=None)

    def execute_planned(self):
        """Update states and build execution properties.

        Besides the update of state for inputs and outcomes, that all
        Operations perform, this also performs the final update of
        Properties on the outcome:

        * application of the ``properties_at_execution`` key of the Assembly
          :attr:`specification`
        * application of :meth:`specific_build_outcome_properties`
          with ``for_exec=True``
        """
        self.check_match_inputs('done')
        # TODO PERF direct update query would probably be faster
        for inp in self.inputs:
            inp.state = 'past'
        outcome = self.outcomes[0]

        outcome.state = 'present'
        goods = outcome.goods
        prop_exprs = merge_state_parameter(
            self.specification.get('outcome_properties'),
            self.state, 'done', 'dict')
        goods.update_properties((k, self.eval_typed_expr(*v))
                                for k, v in prop_exprs.items())
        goods.update_properties(
            self.specific_build_outcome_properties(goods.properties, 'done'))

    def eval_typed_expr(self, etype, expr):
        """Evaluate a typed expression.

        :param expr: the expression to evaluate
        :param etype: the type or ``expr``.

        *Possible values for etype*

        * ``'const'``:
            ``expr`` is considered to be a constant and gets returned
            directly. Any Python value that is JSON serializable is admissible.
        * ``'sequence'``:
            ``expr`` must be the code of a
            ``Model.System.Sequence`` instance. The return value is
            the formatted value of that sequence, after incrementation.
        """
        if etype == 'const':
            return expr
        elif etype == 'sequence':
            return self.registry.System.Sequence.nextvalBy(code=expr.strip())
        raise UnknownExpressionType(self, etype, expr)

    def is_reversible(self):
        """Assembly can be reverted by Unpack.
        """
        return self.outcome_type.get_behaviour("unpack") is not None

    def plan_revert_single(self, dt_execution, follows=()):
        unpack_inputs = [out for op in follows for out in op.outcomes]
        # self.outcomes has actually only those outcomes that aren't inputs
        # of downstream operations
        # TODO maybe change that for API clarity
        unpack_inputs.extend(self.outcomes)
        return self.registry.Wms.Operation.Unpack.create(
            dt_execution=dt_execution,
            inputs=unpack_inputs)
