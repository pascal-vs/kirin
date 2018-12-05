# coding=utf-8

# Copyright (c) 2001-2018, Canal TP and/or its affiliates. All rights reserved.
#
# This file is part of Navitia,
#     the software to build cool stuff with public transport.
#
# Hope you'll enjoy and contribute to this project,
#     powered by Canal TP (www.canaltp.fr).
# Help us simplify mobility and open public transport:
#     a non ending quest to the responsive locomotion way of traveling!
#
# LICENCE: This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Stay tuned using
# twitter @navitia
# IRC #navitia on freenode
# https://groups.google.com/d/forum/navitia
# www.navitia.io

from enum import Enum
from kirin import kirin_pb2


class ModicitationType(Enum):
    add = 1
    delete = 2
    update = 3
    none = 4
    deleted_for_detour = 5
    added_for_detour = 6


def stop_time_status_to_protobuf(stop_time_status):
    return {
        'add': kirin_pb2.ADDED,
        'delete': kirin_pb2.DELETED,
        'update': kirin_pb2.SCHEDULED,
        'none': kirin_pb2.SCHEDULED,
        'deleted_for_detour': kirin_pb2.DELETED_FOR_DETOUR,
        'added_for_detour' : kirin_pb2.ADDED_FOR_DETOUR
    }.get(stop_time_status, kirin_pb2.SCHEDULED)


def get_modification_type_order(modication_type):
    return {
        'nochange': 0,
        'add': 1,
        'added_for_detour': 2,
        'delete': 3,
        'deleted_for_detour': 4,
        'update': 5,
    }.get(modication_type, 0)


class TripEffect(Enum):
    NO_SERVICE = 1,
    REDUCED_SERVICE = 2,
    SIGNIFICANT_DELAYS = 3,
    DETOUR = 4,
    ADDITIONAL_SERVICE = 5,
    MODIFIED_SERVICE = 6,
    OTHER_EFFECT = 7,
    UNKNOWN_EFFECT = 8,
    STOP_MOVED = 9,

