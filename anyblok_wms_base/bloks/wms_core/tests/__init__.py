# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.
from anyblok.tests.testcase import BlokTestCase


class TestCore(BlokTestCase):

    def test_insert_goods_type(self):
        goods_type = self.registry.Wms.Goods.Type.insert(label="My good type")
        self.assertEqual(goods_type.label, "My good type")