# coding=utf-8

# Copyright (c) 2001-2017, Canal TP and/or its affiliates. All rights reserved.
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

from __future__ import absolute_import, print_function, unicode_literals, division
import flask
from flask.globals import current_app
from flask_restful import Resource, marshal, abort
from google.protobuf.message import DecodeError
from kirin.exceptions import InvalidArguments
import navitia_wrapper
from kirin.gtfs_rt import model_maker
from kirin import redis_client
from kirin.utils import manage_db_error
from kirin.core import model
from kirin.core.types import ConnectorType
from kirin.resources.contributors import contributor_fields


def get_gtfsrt_contributors():
    """
    :return: all GTFS-RT contributors from config file + db
    File has priority over db
    TODO: Remove from config file
    """
    contributor_legacy_id = None
    gtfsrt_contributors = []
    if "GTFS_RT_CONTRIBUTOR" in current_app.config and current_app.config.get(str("GTFS_RT_CONTRIBUTOR")):
        contributor_legacy_id = current_app.config.get(str("GTFS_RT_CONTRIBUTOR"))
        contributor_legacy = model.Contributor(
            id=contributor_legacy_id,
            navitia_coverage=current_app.config.get(str("NAVITIA_GTFS_RT_INSTANCE")),
            connector_type=ConnectorType.gtfs_rt.value,
            navitia_token=current_app.config.get(str("NAVITIA_GTFS_RT_TOKEN")),
            feed_url=current_app.config.get(str("GTFS_RT_FEED_URL")),
        )
        gtfsrt_contributors.append(contributor_legacy)

    gtfsrt_contributors.extend(
        [
            c
            for c in model.Contributor.find_by_connector_type(ConnectorType.gtfs_rt.value)
            if c.id != contributor_legacy_id
        ]
    )
    return gtfsrt_contributors


def _get_gtfs_rt(req):
    if not req.data:
        raise InvalidArguments("no gtfs_rt data provided")
    return req.data


def make_navitia_wrapper(contributor):
    return navitia_wrapper.Navitia(
        url=current_app.config.get(str("NAVITIA_URL")),
        token=contributor.navitia_token,
        timeout=current_app.config.get(str("NAVITIA_TIMEOUT"), 5),
        cache=redis_client,
        query_timeout=current_app.config.get(str("NAVITIA_QUERY_CACHE_TIMEOUT"), 600),
        pubdate_timeout=current_app.config.get(str("NAVITIA_PUBDATE_CACHE_TIMEOUT"), 600),
    ).instance(contributor.navitia_coverage)


class GtfsRT(Resource):
    def get(self):
        return {"gtfs-rt": marshal(get_gtfsrt_contributors(), contributor_fields)}

    def post(self, id=None):
        if id is None:
            abort(400, message="Contributor's id is missing")

        contributor = model.Contributor.query_existing().filter_by(id=id).first()
        if not contributor:
            abort(404, message="Contributor '{}' not found".format(id))

        raw_proto = _get_gtfs_rt(flask.globals.request)

        from kirin import gtfs_realtime_pb2

        # create a raw gtfs-rt obj, save the raw protobuf into the db
        proto = gtfs_realtime_pb2.FeedMessage()
        try:
            proto.ParseFromString(raw_proto)
        except DecodeError:
            # We save the non-decodable flux gtfs-rt
            manage_db_error(
                proto,
                "gtfs-rt",
                contributor=contributor.id,
                error="Decode Error",
                is_reprocess_same_data_allowed=False,
            )
            raise InvalidArguments("invalid protobuf")
        else:
            model_maker.handle(proto, make_navitia_wrapper(contributor), contributor.id)
            return {"message": "GTFS-RT feed processed"}, 200
