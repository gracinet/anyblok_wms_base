.. _components:

Components
==========

Anyblok / Wms Base provides several components, what Anyblok calls
"Bloks". This means that you have to install them in the database,
update them etc.

.. _blok_wms_core:

wms-core
--------

As the name suggests, this Blok provides the :ref:`core_concepts` of
Anyblok / Wms.

.. seealso:: :mod:`the code documentation  <anyblok_wms_base.core>`.


.. _blok_wms_reservation:

wms-reservation
---------------

This Blok provides facilities to reserve :ref:`physobj_model`.

Reservations bind :ref:`physobj_model` to some purpose
(typically a final delivery, or a manufacturing action), that
typically gets fulfilled through a *chain* of operations.

.. seealso:: :ref:`the overwiew of reservation concepts,
             <reservation>` and :mod:`the
             code documentation  <anyblok_wms_base.reservation>`.


.. _blok_wms_inventory:

wms-inventory
-------------

This Blok provides facilities to perform inventories, namely to
compare (parts of) the database with reality, and issue appropriate
Operations to correct deviations.

This is by no means the only way to represent such complex processes,
but we think it could be helpful for a good range of
applications. That's why these features are provided in an optional
Blok, shipping with AnyBlok / Wms Base. On the other hand, the
resulting are part of :ref:`the core <blok_wms_core>` (see
:ref:`op_apparition`, :ref:`op_disparition` and :ref:`op_teleportation`).

.. warning:: this Blok is less mature than the rest of AnyBlok / Wms
             Base. Notably, as of version 0.9, the provided
             inventories take only the :ref:`Type <physobj_type>`
             of the Physical Objects
             into account. We expect support for Properties and code
             to appear in subsequent versions. It is also currently
             incompatible with :ref:`blok_wms_quantity`.

To summarize the process, an Inventory is made of a tree of
:class:`Inventory Nodes <anyblok_wms_base.inventory.node.Node>` and
some metadata, such as the reason, the date, restrictions to some
:ref:`Physical Object Types <physobj_type>`…
Each Node is attached to a given location, and
either represents the whole inventory under that location, or is split
in sub Nodes (one for each direct sublocation).

During the process, :class:`Inventory Lines
<anyblok_wms_base.inventory.node.Line>`
are attached to the leaf Nodes, representing the
full assessment of relevant Physical Objects under each Node's
location. It is typically expected that this is the result of
operators walking down the warehouse and taking note of what they find
in there. Then a reconciliation phase computes all deviations in
the form of :class:`Inventory Actions
<anyblok_wms_base.inventory.action.Action>`,
starting from leaf Nodes up to the top.
During the final phase (application), these Actions are converted in
the needed Inventory Operations. At this point, the database contents
matches the assessment.

This process allows to delegate sub-inventories (e.g., a given room or
bay), while simplifying the correcting actions: for instance, if
a given physical object is missing in location A yet an unexpected
equivalent one is found in location B, this is interpreted as an unexpected
change of location (:ref:`op_teleportation`), rather than a
:ref:`op_disparition` / :ref:`op_apparition` pair. This is especially important if
a :ref:`reservation` is held for that object, as it allows it to be carried
over rather than to be broken.

.. _blok_wms_quantity:

wms-quantity
------------

This Blok adds a ``quantity`` field on the :ref:`Wms.PhysObj
<physobj_model>` model, to represent goods handled in bulk or several
identical items in one record.

.. seealso:: :doc:`goods_quantity`

.. _blok_wms_rest_api:

wms-rest-api
------------
.. warning:: development not even started

This Blok will integrate Anyblok / WMS Base with `Anyblok / Pyramid
<https://anyblok-pyramid.readthedocs.io>`_ to provide a RESTful HTTP
API.

.. _blok_wms_bus:

wms-bus
-------
.. warning:: development not even started

This Blok will integrate Anyblok / WMS Base with `Anyblok / Bus
<https://anyblok-bus.readthedocs.io>`_ to provide intercommunication
with other applications.
