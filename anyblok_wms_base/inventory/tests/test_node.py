# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.
from anyblok_wms_base.testing import WmsTestCaseWithPhysObj
from ..exceptions import (NodeStateError,
                          NodeChildrenStateError,
                          )


class InventoryNodeTestCase(WmsTestCaseWithPhysObj):

    def setUp(self):
        super().setUp()
        self.Inventory = self.registry.Wms.Inventory
        self.Node = self.Inventory.Node
        self.Line = self.Inventory.Line
        self.Action = self.Inventory.Action
        self.Arrival = self.Operation.Arrival
        self.avatar.update(location=self.stock, state='present')

    def test_repr(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        self.assertEqual(
            repr(node),
            "Wms.Inventory.Node(id=%d, "
            "inventory_id=%d, location_code='STOCK')" % (node.id, inventory.id))

    def test_forbid_partial_splitting(self):
        stock = self.stock
        inventory = self.Inventory.create(location=stock)
        node = inventory.root

        # we could use stock again, but it's better to use an appropriate
        # location to stress that the problem is the wild creation of a subnode
        sub = self.insert_location('SUB', parent=stock)
        self.insert_location('SUB2', parent=stock)

        with self.assertRaises(NotImplementedError):
            self.Node.insert(parent=node, location=sub)

        with self.assertRaises(NotImplementedError):
            self.Node(parent=node, location=sub)

    def test_split(self):
        stock = self.stock
        inventory = self.Inventory.create(location=stock)
        node = inventory.root
        self.assertTrue(node.is_leaf)
        loc_a = self.insert_location("A", parent=stock)
        # to make things more interesting, let's use a sub container Type
        # in the current implementation, this exerts a recursive CTE
        loc_b = self.insert_location("B",
                                     parent=stock,
                                     location_type=self.PhysObj.Type.insert(
                                         code='SUBLOC', parent=stock.type))
        # to check that only direct sublocations are taken into account
        self.insert_location("AA", parent=loc_a)

        # check assumption: self.physobj is currently in stock as well
        self.assertEqual(self.avatar.location, stock)
        self.assertEqual(self.avatar.state, 'present')

        children = node.split()
        self.assertFalse(node.is_leaf)
        self.assertEqual(len(children), 2)

        # in particular, there's no child created for self.physobj
        self.assertEqual(set(children),
                         set(self.Node.query().filter_by(parent=node).all()))
        self.assertEqual(set(c.location for c in children),
                         {loc_a, loc_b})
        self.assertTrue(all(c.inventory == inventory for c in children))

    def test_compute_actions_not_enough_phobjs(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        node.state = 'full'
        self.Line.insert(node=node,
                         location=self.stock,
                         type=self.physobj.type,
                         quantity=2)

        node.compute_actions()
        action = self.single_result(self.Action.query().filter_by(node=node))
        self.assertEqual(action.type, 'app')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, self.physobj.type)
        self.assertEqual(action.quantity, 1)

        self.assertIsNone(action.physobj_code)
        self.assertIsNone(action.physobj_properties)

    def test_compute_actions_considered_types(self):
        """considered_types field of Inventory has the expected consequences.

        We create a new `PhysObj` with a new `Type`, and an `Inventory` with
        `considered_types` restricting to the existing `self.physobj_type`.
        The new `PhysObj` is dully ignored.

        Conversely, switching back `considered_types` to the new Type
        has the effect of generate the expected `Inventory.Action` for that
        Type and ignoring the `Inventory.Line` about the old Type.
        """
        other_type = self.PhysObj.Type.insert(code='other')
        self.Arrival.create(state='done',
                            location=self.stock,
                            physobj_type=other_type,
                            )

        pot = self.physobj.type
        inventory = self.Inventory.create(location=self.stock,
                                          considered_types=[pot.code])
        node = inventory.root
        node.state = 'full'
        self.Line.insert(node=node,
                         location=self.stock,
                         type=pot,
                         quantity=1)

        node.compute_actions()
        self.assertIsNone(self.Action.query().filter_by(node=node).first())

        # now let's consider the new type, without a line for the phobj of
        # that type, we get a disparition
        inventory.considered_types = [other_type.code]
        node.state = 'full'
        node.compute_actions()
        action = self.single_result(self.Action.query().filter_by(node=node))

        self.assertEqual(action.type, 'disp')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, other_type)
        self.assertEqual(action.quantity, 1)

        self.assertIsNone(action.physobj_code)
        self.assertIsNone(action.physobj_properties)

    def test_compute_actions_excluded_types(self):
        """excluded_types field of Inventory has the expected consequences.

        Same setup and assertions as
        :meth:`test_compute_actions_considered_types`,
        using the `exclude_types` field instead of `considered_types`
        """
        other_type = self.PhysObj.Type.insert(code='other')
        self.Arrival.create(state='done',
                            location=self.stock,
                            physobj_type=other_type,
                            )

        pot = self.physobj.type
        inventory = self.Inventory.create(location=self.stock,
                                          excluded_types=[other_type.code])
        node = inventory.root
        node.state = 'full'
        self.Line.insert(node=node,
                         location=self.stock,
                         type=pot,
                         quantity=1)

        node.compute_actions()
        self.assertIsNone(self.Action.query().filter_by(node=node).first())

        # now let's stop excluding `other_type`. Without a line for the phobj
        # of that type, we get a disparition
        inventory.excluded_types = [pot.code]
        node.state = 'full'
        node.compute_actions()
        action = self.single_result(self.Action.query().filter_by(node=node))

        self.assertEqual(action.type, 'disp')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, other_type)
        self.assertEqual(action.quantity, 1)

        self.assertIsNone(action.physobj_code)
        self.assertIsNone(action.physobj_properties)

    def test_compute_actions_apparition_below(self):
        sub = self.insert_location("SUB", parent=self.stock)
        inventory = self.Inventory.create(
            location=self.stock,
            excluded_types=[self.location_type.code])

        root = inventory.root
        for subnode in root.split():
            subnode.state = 'pushed'
        node = self.Node.query().filter_by(location=sub).one()
        self.Line.insert(node=node,
                         location=sub,
                         type=self.physobj.type,
                         quantity=2)

        root.state = 'full'
        root.compute_actions()

        # the line of node doesn't affect root node results, only
        # our usual phobj leads a disparition (since root has no Lines)
        action = self.single_result(self.Action.query().filter_by(node=root))
        self.assertEqual(action.type, 'disp')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, self.physobj.type)
        self.assertEqual(action.quantity, 1)

    def test_compute_actions_too_many_phobjs(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        node.state = 'full'
        # of course, in real life it's more probable we wouldn't have
        # a line at all, but there's no real value to bother creating
        # a new physobj to compare 1 and 2 instead of 0 and 1
        self.Line.insert(node=node,
                         location=self.stock,
                         type=self.physobj.type,
                         quantity=0)

        node.compute_actions()
        action = self.single_result(self.Action.query().filter_by(node=node))
        self.assertEqual(action.type, 'disp')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, self.physobj.type)
        self.assertEqual(action.quantity, 1)

        self.assertIsNone(action.physobj_code)
        self.assertIsNone(action.physobj_properties)

    def test_compute_actions_all_matching(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        node.state = 'full'
        node_actions_q = self.Action.query().filter_by(node=node)

        self.Line.insert(node=node,
                         location=self.stock,
                         type=self.physobj.type,
                         quantity=1)
        node.compute_actions()
        self.assertEqual(node_actions_q.count(), 0)
        self.assertEqual(node.state, 'computed')

        self.Action.insert(type='app',
                           node=node,
                           location=self.stock,
                           physobj_type=self.physobj.type,
                           quantity=3)
        node.compute_actions(recompute=True)
        self.assertEqual(node_actions_q.count(), 0)
        self.assertEqual(node.state, 'computed')

    def test_compute_actions_wrong_phobj_code(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        node.state = 'full'
        line = self.Line.insert(node=node,
                                location=self.stock,
                                type=self.physobj.type,
                                quantity=1,
                                code='Not on self.physobj!')

        node.compute_actions()
        actions = (self.Action.query()
                   .filter_by(node=node)
                   .order_by(self.Action.type)
                   .all())

        # it boils down to an Apparition and a Disparition at the same place

        for action in actions:
            self.assertEqual(action.location, self.stock)
            self.assertEqual(action.physobj_type, self.physobj.type)
            self.assertEqual(action.quantity, 1)
            self.assertIsNone(action.physobj_properties)

        app, disp = actions
        self.assertEqual(app.type, 'app')
        self.assertEqual(app.physobj_code, line.code)
        self.assertEqual(disp.type, 'disp')
        self.assertIsNone(disp.physobj_code)

    def test_compute_actions_avatar_elsewhere(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        node.state = 'full'
        self.avatar.location = self.incoming_loc
        self.Line.insert(node=node,
                         location=self.stock,
                         type=self.physobj.type,
                         quantity=1)

        node.compute_actions()

        action = self.single_result(self.Action.query().filter_by(node=node))
        self.assertEqual(action.type, 'app')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, self.physobj.type)
        self.assertEqual(action.quantity, 1)

        self.assertIsNone(action.physobj_code)
        self.assertIsNone(action.physobj_properties)

    def test_compute_actions_missing_line(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        node.state = 'full'
        node.compute_actions()

        action = self.single_result(self.Action.query().filter_by(node=node))
        self.assertEqual(action.type, 'disp')

        self.assertEqual(action.location, self.stock)
        self.assertEqual(action.physobj_type, self.physobj.type)
        self.assertEqual(action.quantity, 1)

        self.assertIsNone(action.physobj_code)
        self.assertIsNone(action.physobj_properties)

    def test_compute_actions_leaf_sublocs(self):
        stock = self.stock
        pot = self.physobj_type
        loc_a = self.insert_location("A", parent=stock)
        loc_b = self.insert_location("B", parent=stock)
        # this also tests the Type exclusion (for the location type)
        inventory = self.Inventory.create(
            location=stock,
            excluded_types=[self.location_type.code])
        node = inventory.root
        node.state = 'full'

        self.Line.insert(node=node, location=stock, type=pot, quantity=2)

        Arrival = self.Operation.Arrival
        Arrival.create(state='done', location=loc_a, physobj_type=pot)
        Arrival.create(state='done', location=loc_b, physobj_type=pot,
                       physobj_code='in_b')
        self.Line.insert(node=node, location=loc_b, type=pot, quantity=2)

        node.compute_actions()
        Action = self.Action

        def node_actions():
            return {
                (a.type, a.location, a.destination,
                 a.physobj_type, a.physobj_code, a.quantity)
                for a in Action.query().filter_by(node=node).all()}

        self.assertEqual(node_actions(),
                         {('app', stock, None, pot, None, 1),
                          ('disp', loc_a, None, pot, None, 1),
                          ('app', loc_b, None, pot, None, 2),
                          ('disp', loc_b, None, pot, 'in_b', 1),
                          })
        Action.simplify(node)
        # TODO this is non deterministic: one can also take one of the
        # two appearing at loc_b to match the disparition at loc_a
        self.assertEqual(node_actions(),
                         {('telep', loc_a, stock, pot, None, 1),
                          ('app', loc_b, None, pot, None, 2),
                          ('disp', loc_b, None, pot, 'in_b', 1),
                          })

    def test_compute_actions_split_sublocs(self):
        stock = self.stock
        pot = self.physobj_type
        loc_a = self.insert_location("A", parent=stock)
        loc_b = self.insert_location("B", parent=stock)
        # this also tests the Type exclusion (for the location type)
        inventory = self.Inventory.create(
            location=stock,
            excluded_types=[self.location_type.code])
        node = inventory.root
        for sub in node.split():
            sub.state = 'pushed'
        node.state = 'full'

        self.Line.insert(node=node, location=stock, type=pot, quantity=2)

        # The split Node doesn't care about Physical Objects that are
        # in the sublocations since they are the responsibility of the subnodes
        # these will be ignored
        # (compare with test_compute_actions_leaf_sublocs):
        Arrival = self.Operation.Arrival
        Arrival.create(state='done', location=loc_a, physobj_type=pot)
        Arrival.create(state='done', location=loc_b, physobj_type=pot,
                       physobj_code='in_b')

        node.compute_actions()

        action = self.single_result(self.Action.query().filter_by(node=node))

        self.assertEqual(action.type, 'app')
        self.assertEqual(action.location, stock)
        self.assertEqual(action.physobj_type, pot)
        self.assertEqual(action.quantity, 1)

    def test_compute_actions_wrong_states(self):
        inventory = self.Inventory.create(location=self.stock)
        node = inventory.root
        with self.assertRaises(NodeStateError) as arc:
            node.compute_actions()

        exc = arc.exception
        str(exc)
        repr(exc)
        self.assertEqual(exc.node_id, node.id)
        self.assertEqual(exc.node_state, 'draft')

        node.state = 'reconciled'
        with self.assertRaises(NodeStateError) as arc:
            node.compute_actions()

        exc = arc.exception
        str(exc)
        repr(exc)
        self.assertEqual(exc.node_id, node.id)
        self.assertEqual(exc.node_state, 'reconciled')

    def fixture_compute_push_actions(self):
        inventory = self.Inventory.create(
            location=self.stock,
            excluded_types=[self.location_type.code])
        root = inventory.root

        pot = self.physobj_type
        loc_a = self.insert_location("A", parent=self.stock)

        loc_aa = self.insert_location("AA", parent=loc_a)
        loc_ab = self.insert_location("AB", parent=loc_a)

        root.split()
        node = self.Node.query().filter_by(location=loc_a).one()

        self.Arrival.create(state='done', location=loc_aa, physobj_type=pot)
        self.Line.insert(node=node, location=loc_ab, type=pot, quantity=2)
        return inventory, root, node

    def test_compute_push_actions(self):
        pot = self.physobj_type
        inventory, root, node = self.fixture_compute_push_actions()

        node.state = 'full'
        node.compute_push_actions()

        self.assertEqual(node.state, 'pushed')

        Action = self.Action

        telep = self.single_result(Action.query().filter_by(node=node))
        self.assertEqual(telep.type, 'telep')
        self.assertEqual(telep.physobj_type, pot)
        self.assertEqual(telep.location.code, 'AA')
        self.assertEqual(telep.destination.code, 'AB')
        self.assertEqual(telep.quantity, 1)

        app = self.single_result(Action.query().filter_by(node=root))
        self.assertEqual(app.type, 'app')
        self.assertEqual(app.physobj_type, pot)
        self.assertEqual(app.location.code, 'AB')
        self.assertEqual(app.quantity, 1)

    def test_recurse_compute_push_actions(self):
        pot = self.physobj_type
        inventory, root, node = self.fixture_compute_push_actions()

        # There's an object in self.stock, yet no Line for root Node,
        # so that's a disparition in self.stock

        root.state = 'full'
        node.state = 'full'
        root.recurse_compute_push_actions()

        self.assertEqual(node.state, 'pushed')
        self.assertEqual(root.state, 'pushed')

        Action = self.Action

        telep = self.single_result(Action.query().filter_by(node=node))
        self.assertEqual(telep.type, 'telep')
        self.assertEqual(telep.physobj_type, pot)
        self.assertEqual(telep.location.code, 'AA')
        self.assertEqual(telep.destination.code, 'AB')
        self.assertEqual(telep.quantity, 1)

        # the apparition at loc_ab has been resolved with the disparition
        # at self.stock into a teleportation

        app = self.single_result(Action.query().filter_by(node=root))
        self.assertEqual(app.type, 'telep')
        self.assertEqual(app.physobj_type, pot)
        self.assertEqual(app.location, self.stock)
        self.assertEqual(app.destination.code, 'AB')
        self.assertEqual(app.quantity, 1)

    def test_recurse_compute_push_actions_non_ready_child(self):
        inventory, root, node = self.fixture_compute_push_actions()
        root.state = 'full'
        with self.assertRaises(NodeChildrenStateError) as arc:
            root.recurse_compute_push_actions()

        exc = arc.exception
        str(exc)
        repr(exc)
        self.assertEqual(exc.node, root)
        self.assert_singleton(exc.children, value=node)

    def test_compute_actions_non_ready_child(self):
        inventory, root, node = self.fixture_compute_push_actions()
        root.state = 'full'
        node.state = 'computed'  # that's not enough
        with self.assertRaises(NodeChildrenStateError) as arc:
            root.compute_actions()

        exc = arc.exception
        str(exc)
        repr(exc)
        self.assertEqual(exc.node, root)
        self.assert_singleton(exc.children, value=node)


del WmsTestCaseWithPhysObj
