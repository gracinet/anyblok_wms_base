# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.
from sqlalchemy import orm
from sqlalchemy import or_
from sqlalchemy import and_
from sqlalchemy import not_
from sqlalchemy import func

from anyblok import Declarations
from anyblok.column import Integer
from anyblok.column import Text
from anyblok.column import Selection
from anyblok.relationship import Many2One
from anyblok_postgres.column import Jsonb

register = Declarations.register
Wms = Declarations.Model.Wms


@register(Wms.Inventory)
class Node:
    """Representation of the inventory of a subtree of containment hierarchy.

    For each Inventory, there's a tree of Inventory Nodes, each Node
    having one-to-many relationships to:

    - :class:`Inventory Lines <Line>` that together with its descendants',
      form the whole assessment of the contents of the Node's
      :attr:`location`
    - :class:`Inventory Actions <Action>` that encode the primary Operations
      that have to be executed to reconcile the database with the assessment.

    Each Node has a :attr:`location` under which the `locations <location>` of
    its children should be directly placed, but that doesn't mean each
    container visited by the inventory process has to be represented by a
    Node: instead, for each Inventory, the
    :attr:`locations <location>` of its leaf Nodes would ideally balance
    the amount of assessment work that can be done by one person in a
    continuous manner while keeping the size of the tree reasonible.

    Applications may want to override this Model to add user fields,
    representing who's in charge of a given node. The user would then either
    optionally take care of splitting (issuing children) the Node and perform
    assesments that are not covered by children Nodes.

    This whole structure is designed so that assessment work can be
    distributed and reconciliation can be performed in parallel.
    """
    STATES = (
        ('draft', 'wms_inventory_state_draft'),
        ('full', 'wms_inventory_state_full'),
        ('computed', 'wms_inventory_state_computed'),
        ('reconciled', 'wms_inventory_state_reconciled'),
    )

    id = Integer(label="Identifier", primary_key=True)
    """Primary key."""

    state = Selection(selections=STATES,
                      nullable=False,
                      default='draft',
                      )
    """Node lifecycle

    - draft:
        the Node has been created, could still be split, but its
        :class:`lines <Line>` don't represent the full contents yet.
    - assessment:
        (TODO not there yet, do we need it?) assessment work
        has started.
    - full:
        all Physical Objects relevant to the Inventory that are below
        :attr:`location` are accounted for in the :class:`lines <Line>` of
        its Nodes or of its descendants. This implies in particular that
        none of the children Nodes is in prior states.
    - computed:
        all :class:`Actions <Action>` to reconcile the database with the
        assessment have been issued. It is still possible to simplify them
        (that would typically be the :attr:`parent`'s responsibility)
    - reconciled:
        all relevant Operations have been issued.
    """

    inventory = Many2One(model=Wms.Inventory,
                         index=True,
                         nullable=False)
    """The Inventory for which this Node has been created"""

    parent = Many2One(model='Model.Wms.Inventory.Node',
                      index=True)
    location = Many2One(model=Wms.PhysObj, nullable=False)

    def __init__(self, parent=None, from_split=False, **fields):
        """Forbid creating subnodes if not from :meth:`split`

        Partially split Inventory Nodes are currently not consistent
        in their computation of reconciliation Actions.
        """
        if parent is not None and not from_split:
            raise NotImplementedError("Partially split Inventory Nodes are "
                                      "currently not supported. Please use "
                                      "Node.split() to create subnodes")
        super().__init__(parent=parent, **fields)

    @property
    def is_leaf(self):
        """(:class:`bool`): ``True`` if and only if the Node has no children.
        """
        return self.query().filter_by(parent=self).count() == 0

    def split(self):
        """Create a child Node for each container in :attr:`location`."""
        PhysObj = self.registry.Wms.PhysObj
        Avatar = PhysObj.Avatar
        ContainerType = orm.aliased(
            PhysObj.Type.query_behaviour('container', as_cte=True),
            name='container_type')
        subloc_query = (PhysObj.query()
                        .join(Avatar.obj)
                        .join(ContainerType,
                              ContainerType.c.id == PhysObj.type_id)
                        .filter(Avatar.state == 'present'))
        return [self.insert(inventory=self.inventory,
                            from_split=True,
                            parent=self,
                            location=container)
                for container in subloc_query.all()]

    def compute_actions(self, recompute=False):
        """Create :class:`Action` to reconcile database with assessment.

        :param bool recompute: if ``True``, can be applied even if
                               :attr:`state` is already 'computed' or later.

        Implementation and performance details:

        Internally, this uses an SQL query that's quite heavy:

        - recursive CTE for the sublocations
        - that's joined with Avatar and PhysObj to extract quantities
          and information (type, code, properties)
        - on top of that, full outer join with Inventory.Line

        but it has advantages:

        - works uniformely in the three cases:

          + no Inventory.Line matching a given Avatar
          + no Avatar matching a given Inventory.Line
          + a given Inventory.Line has matching Avatars, but the counts
            don't match
        - minimizes round-trip to the database
        - minimizes Python side processing
        """
        state = self.state
        if state in ('draft', 'assessment'):
            # TODO precise exc
            raise ValueError("Can't compute actions on Node id=%d (state=%r) "
                             "that hasn't reached the 'full' state'" % (
                                 self.id, state))
        if state in ('computed', 'reconciled'):
            if recompute:
                self.clear_actions()
            else:
                # TODO precise exc
                raise ValueError("Can't compute actions on "
                                 "Node id=%d (state=%r) "
                                 "that's already past the 'full' state'" % (
                                     self.id, state))

        PhysObj = self.registry.Wms.PhysObj
        POType = PhysObj.Type
        Avatar = PhysObj.Avatar
        Inventory = self.registry.Wms.Inventory
        Line = Inventory.Line
        Action = Inventory.Action

        excluded_types = self.inventory.excluded_types
        if not excluded_types:
            phobj_filter = None
        else:
            def phobj_filter(query):
                excluded_types_q = (POType.query(POType.id)
                                    .filter(POType.code.in_(excluded_types)))
                return query.filter(not_(PhysObj.type_id.in_(excluded_types_q)))

        cols = (Avatar.location_id, PhysObj.code, PhysObj.type_id)
        quantity_query = self.registry.Wms.quantity_query
        existing_phobjs = (quantity_query(location=self.location,
                                          location_recurse=self.is_leaf,
                                          additional_filter=phobj_filter)
                           .add_columns(*cols).group_by(*cols)
                           .subquery())

        comp_query = (
            Line.query()
            .join(existing_phobjs,
                  # multiple criteria to join on the subquery would fail,
                  # complaining of lack of foreign key (SQLA bug maybe)?
                  # but it works with and_()
                  and_(Line.type_id == existing_phobjs.c.type_id,
                       Line.location_id == existing_phobjs.c.location_id,
                       or_(Line.code == existing_phobjs.c.code,
                           and_(existing_phobjs.c.code.is_(None),
                                Line.code.is_(None)))),
                  full=True)
            .filter(func.coalesce(existing_phobjs.c.qty, 0) !=
                    func.coalesce(Line.quantity, 0))
            .add_columns(func.coalesce(existing_phobjs.c.qty, 0)
                         .label('phobj_qty'),
                         # these columns are useful only if Line is None:
                         existing_phobjs.c.location_id.label('phobj_loc'),
                         existing_phobjs.c.type_id.label('phobj_type'),
                         existing_phobjs.c.code.label('phobj_code'),
                         ))

        for row in comp_query.all():
            line, phobj_count = row[:2]
            if line is None:
                Action.insert(node=self,
                              type='disp',
                              quantity=phobj_count,
                              location=PhysObj.query().get(row[2]),
                              physobj_type=POType.query().get(row[3]),
                              physobj_code=row[4],
                              )
                continue

            diff_qty = phobj_count - line.quantity
            fields = dict(node=self,
                          location=line.location,
                          physobj_type=line.type,
                          physobj_code=line.code,
                          physobj_properties=line.properties)

            # the query is tailored so that diff_qty is never 0
            if diff_qty > 0:
                Action.insert(type='disp', quantity=diff_qty, **fields)
            else:
                Action.insert(type='app', quantity=-diff_qty, **fields)

        # TODO Teleportations
        self.state = 'computed'

    def clear_actions(self):
        (self.registry.Wms.Inventory.Action.query()
         .filter_by(node=self)
         .delete(synchronize_session='fetch'))


@register(Wms.Inventory)
class Line:
    """Represent an assessment for a :class:`Node <Node>` instance."""
    id = Integer(label="Identifier", primary_key=True)
    """Primary key."""
    node = Many2One(model=Wms.Inventory.Node,
                    one2many='lines',
                    nullable=False)
    location = Many2One(model=Wms.PhysObj, nullable=False)
    type = Many2One(model=Wms.PhysObj.Type, nullable=False)
    code = Text()
    properties = Jsonb()
    quantity = Integer(nullable=False)


@register(Wms.Inventory)
class Action:
    """Represent a reconciliation Action for a :class:`Node <Node>` instance.

    TODO data design
    """
    id = Integer(label="Identifier", primary_key=True)
    """Primary key."""
    node = Many2One(model=Wms.Inventory.Node,
                    one2many='actions',
                    nullable=False)

    OPERATIONS = (
        ('app', 'wms_inventory_action_app'),
        ('disp', 'wms_inventory_action_disp'),
        ('telep', 'wms_inventory_action_telep'),
    )

    type = Selection(selections=OPERATIONS, nullable=False)

    location = Many2One(model=Wms.PhysObj, nullable=False)
    destination = Many2One(model=Wms.PhysObj)
    """Optional destination container.

    This is useful if :attr:`type` is ``telep`` only.
    """
    physobj_type = Many2One(model=Wms.PhysObj.Type, nullable=False)
    physobj_code = Text()
    physobj_properties = Jsonb()
    quantity = Integer(nullable=False)