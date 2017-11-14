# Copyright (c) 2001-2017, Canal TP and/or its affiliates. All rights reserved.
#
# This file is part of Navitia,
#     the software to build cool stuff with public transport.
#
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
from datetime import timedelta
import datetime
import pytest
from pytz import utc
from kirin.core.model import RealTimeUpdate, db, TripUpdate, StopTimeUpdate
from kirin.core.populate_pb import to_posix_time, convert_to_gtfsrt
from kirin import gtfs_rt
from tests import mock_navitia
from tests.check_utils import dumb_nav_wrapper, api_post
from kirin import gtfs_realtime_pb2, app


@pytest.fixture(scope='function', autouse=True)
def navitia(monkeypatch):
    """
    Mock all calls to navitia for this fixture and get_publication_date
    """
    monkeypatch.setattr('navitia_wrapper._NavitiaWrapper.query', mock_navitia.mock_navitia_query)
    monkeypatch.setattr('navitia_wrapper._NavitiaWrapper.get_publication_date', mock_navitia.mock_publication_date)


@pytest.fixture(scope='function')
def mock_rabbitmq(monkeypatch):
    """
    Mock all calls to navitia for this fixture
    """
    from mock import MagicMock
    mock_amqp = MagicMock()
    monkeypatch.setattr('kombu.messaging.Producer.publish', mock_amqp)
    return mock_amqp


@pytest.fixture()
def basic_gtfs_rt_data():
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=15, hour=15))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-R-vj1"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 180
    stu.stop_sequence = 4
    stu.stop_id = "Code-StopR4"

    return feed


def test_wrong_gtfs_rt_post():
    """
    wrong protobuf post on the api
    """
    res, status = api_post('/gtfs_rt', check=False, data='bob')

    assert status == 400

    print res.get('error') == 'invalid'


def test_gtfs_rt_post_no_data():
    """
    when no data is given, we got a 400 error
    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt')
    assert resp.status_code == 400

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 0
        assert len(TripUpdate.query.all()) == 0
        assert len(StopTimeUpdate.query.all()) == 0


def test_gtfs_model_builder(basic_gtfs_rt_data):
    """
    test the model builder with a simple gtfs-rt

    we have realtime data on only 2 stops, so the model builder should only have 2 stops (even if the VJ
    have 4 stops)
    """
    with app.app_context():
        data = ''
        rt_update = RealTimeUpdate(data, connector='gtfs-rt', contributor='realtime.gtfs')
        trip_updates = gtfs_rt.KirinModelBuilder(dumb_nav_wrapper()).build(rt_update, basic_gtfs_rt_data)

        # we associate the trip_update manually for sqlalchemy to make the links
        rt_update.trip_updates = trip_updates
        db.session.add(rt_update)
        db.session.commit()

        assert len(trip_updates) == 1
        assert len(trip_updates[0].stop_time_updates) == 2

        second_stop = trip_updates[0].stop_time_updates[0]
        assert second_stop.stop_id == 'StopR2'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival_delay == timedelta(minutes=1)
        assert second_stop.departure_delay is None
        assert second_stop.departure_status == 'none'
        assert second_stop.message is None

        fourth_stop = trip_updates[0].stop_time_updates[1]
        assert fourth_stop.stop_id == 'StopR4'
        assert fourth_stop.arrival_status == 'update'
        assert fourth_stop.arrival_delay == timedelta(minutes=3)
        assert fourth_stop.departure_delay is None
        assert fourth_stop.departure_status == 'none'
        assert fourth_stop.message is None

        feed = convert_to_gtfsrt(trip_updates)
        assert feed.entity[0].trip_update.trip.start_date == u'20120615' #must be UTC start date


def test_gtfs_rt_simple_delay(basic_gtfs_rt_data, mock_rabbitmq):
    """
    test the gtfs-rt post with a simple gtfs-rt

    we have realtime data on only 2 stops, so the model builder should only have 2 stops (even if the VJ
    have 4 stops)

    after the merge, we should have 4 stops (and only 2 delayed)
    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=basic_gtfs_rt_data.SerializeToString())
    assert resp.status_code == 200

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 4

        trip_update = TripUpdate.find_by_dated_vj('R:vj1', datetime.datetime(2012, 6, 15, 14, 0))

        assert trip_update

        # navitia's time are in local, but we return UTC time, and the stop is in sherbrooke, so UTC-4h
        first_stop = trip_update.stop_time_updates[0]
        assert first_stop.stop_id == 'StopR1'
        assert first_stop.arrival_status == 'none'
        assert first_stop.arrival_delay == timedelta(0)
        assert first_stop.arrival == datetime.datetime(2012, 6, 15, 14, 00)
        assert first_stop.departure_delay == timedelta(0)
        assert first_stop.departure_status == 'none'
        assert first_stop.departure == datetime.datetime(2012, 6, 15, 14, 00)
        assert first_stop.message is None

        second_stop = trip_update.stop_time_updates[1]
        assert second_stop.stop_id == 'StopR2'
        assert second_stop.arrival_status == 'update'
        # 10:30 in local + 4h to get it in UTC + 1minute of delay
        assert second_stop.arrival == datetime.datetime(2012, 6, 15, 14, 31)
        assert second_stop.arrival_delay == timedelta(minutes=1)
        # even if the GTFS-RT has no information of the departure, it have been also delayed by 1mn
        # for coherence
        assert second_stop.departure == datetime.datetime(2012, 6, 15, 14, 31)
        assert second_stop.departure_delay == timedelta(minutes=1)
        assert second_stop.departure_status == 'none'
        assert second_stop.message is None

        third_stop = trip_update.stop_time_updates[2]
        assert third_stop.stop_id == 'StopR3'
        assert third_stop.arrival_status == 'none'
        assert third_stop.arrival_delay == timedelta(0)
        assert third_stop.arrival == datetime.datetime(2012, 6, 15, 15, 00)
        assert third_stop.departure_delay == timedelta(0)
        assert third_stop.departure_status == 'none'
        assert third_stop.departure == datetime.datetime(2012, 6, 15, 15, 00)
        assert third_stop.message is None

        fourth_stop = trip_update.stop_time_updates[3]
        assert fourth_stop.stop_id == 'StopR4'
        assert fourth_stop.arrival_status == 'update'
        assert fourth_stop.arrival_delay == timedelta(minutes=3)
        assert fourth_stop.arrival == datetime.datetime(2012, 6, 15, 15, 33)
        # even if the GTFS-RT has no information of the departure, it have been also delayed by 3mn
        # for coherence
        assert fourth_stop.departure_delay == timedelta(minutes=3)
        assert fourth_stop.departure_status == 'none'
        assert fourth_stop.departure == datetime.datetime(2012, 6, 15, 15, 33)
        assert fourth_stop.message is None


@pytest.fixture()
def pass_midnight_gtfs_rt_data():
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=16, hour=5))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-pass-midnight"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 1
    stu.stop_id = "Code-StopR1"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 150
    stu.departure.delay = 150
    stu.stop_sequence = 3
    stu.stop_id = "Code-StopR2-bis"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 180
    stu.departure.delay = 180
    stu.stop_sequence = 4
    stu.stop_id = "Code-StopR3"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 240
    stu.departure.delay = 240
    stu.stop_sequence = 5
    stu.stop_id = "Code-StopR4"

    return feed


def test_gtfs_pass_midnight_model_builder(pass_midnight_gtfs_rt_data):
    """
    test the model builder with a pass-midnight gtfs-rt
    """
    with app.app_context():
        data = ''
        rt_update = RealTimeUpdate(data, connector='gtfs-rt', contributor='realtime.gtfs')
        trip_updates = gtfs_rt.KirinModelBuilder(dumb_nav_wrapper()).build(rt_update, pass_midnight_gtfs_rt_data)

        # we associate the trip_update manually for sqlalchemy to make the links
        rt_update.trip_updates = trip_updates
        db.session.add(rt_update)
        db.session.commit()

        assert len(trip_updates) == 1
        assert len(trip_updates[0].stop_time_updates) == 5

        first_stop = trip_updates[0].stop_time_updates[0]
        assert first_stop.stop_id == 'StopR1'
        assert first_stop.arrival_status == 'update'
        assert first_stop.arrival_delay == timedelta(minutes=1)
        assert first_stop.departure_status == 'update'
        assert first_stop.departure_delay == timedelta(minutes=1)
        assert first_stop.message is None

        second_stop = trip_updates[0].stop_time_updates[1]
        assert second_stop.stop_id == 'StopR2'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival_delay == timedelta(minutes=1)
        assert second_stop.departure_status == 'update'
        assert second_stop.departure_delay == timedelta(minutes=1)
        assert second_stop.message is None

        second_stop = trip_updates[0].stop_time_updates[2]
        assert second_stop.stop_id == 'StopR2-bis'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival_delay == timedelta(minutes=2, seconds=30)
        assert second_stop.departure_status == 'update'
        assert second_stop.departure_delay == timedelta(minutes=2, seconds=30)
        assert second_stop.message is None

        third_stop = trip_updates[0].stop_time_updates[3]
        assert third_stop.stop_id == 'StopR3'
        assert third_stop.arrival_status == 'update'
        assert third_stop.arrival_delay == timedelta(minutes=3)
        assert third_stop.departure_status == 'update'
        assert third_stop.departure_delay == timedelta(minutes=3)
        assert third_stop.message is None

        fourth_stop = trip_updates[0].stop_time_updates[4]
        assert fourth_stop.stop_id == 'StopR4'
        assert fourth_stop.arrival_status == 'update'
        assert fourth_stop.arrival_delay == timedelta(minutes=4)
        assert fourth_stop.departure_status == 'update'
        assert fourth_stop.departure_delay == timedelta(minutes=4)
        assert fourth_stop.message is None

        feed = convert_to_gtfsrt(trip_updates)
        assert feed.entity[0].trip_update.trip.start_date == u'20120616' #must be UTC start date


def test_gtfs_rt_pass_midnight(pass_midnight_gtfs_rt_data, mock_rabbitmq):
    """
    test the gtfs-rt post with a pass-midnight gtfs-rt

    we have realtime data on only the 4 stops

    after the merge, we should have 4 stops properly delayed
    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=pass_midnight_gtfs_rt_data.SerializeToString())
    assert resp.status_code == 200

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 5

        trip_update = TripUpdate.find_by_dated_vj('R:vj1', datetime.datetime(2012, 6, 16, 3, 30))

        assert trip_update

        assert trip_update.vj.get_start_timestamp() == datetime.datetime(2012, 6, 16, 3, 30, tzinfo=utc)

        # navitia's time are in local, but we return UTC time, and the stop is in sherbrooke, so UTC-4h
        first_stop = trip_update.stop_time_updates[0]
        assert first_stop.stop_id == 'StopR1'
        assert first_stop.arrival_status == 'update'
        assert first_stop.arrival_delay == timedelta(minutes=1)
        # 23:30 in local + 4h to get it in UTC + 1minute of delay
        assert first_stop.arrival == datetime.datetime(2012, 6, 16, 03, 31)
        assert first_stop.departure_delay == timedelta(minutes=1)
        assert first_stop.departure_status == 'update'
        assert first_stop.departure == datetime.datetime(2012, 6, 16, 03, 31)
        assert first_stop.message is None

        second_stop = trip_update.stop_time_updates[1]
        assert second_stop.stop_id == 'StopR2'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival == datetime.datetime(2012, 6, 16, 04, 00)
        assert second_stop.arrival_delay == timedelta(minutes=1)
        assert second_stop.departure == datetime.datetime(2012, 6, 16, 04, 01)
        assert second_stop.departure_delay == timedelta(minutes=1)
        assert second_stop.departure_status == 'update'
        assert second_stop.message is None

        second_stop = trip_update.stop_time_updates[2]
        assert second_stop.stop_id == 'StopR2-bis'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival == datetime.datetime(2012, 6, 16, 04, 02, 30)
        assert second_stop.arrival_delay == timedelta(minutes=2, seconds=30)
        assert second_stop.departure == datetime.datetime(2012, 6, 16, 04, 02, 30)
        assert second_stop.departure_delay == timedelta(minutes=2, seconds=30)
        assert second_stop.departure_status == 'update'
        assert second_stop.message is None

        third_stop = trip_update.stop_time_updates[3]
        assert third_stop.stop_id == 'StopR3'
        assert third_stop.arrival_status == 'update'
        assert third_stop.arrival_delay == timedelta(minutes=3)
        assert third_stop.arrival == datetime.datetime(2012, 6, 16, 04, 03)
        assert third_stop.departure_delay == timedelta(minutes=3)
        assert third_stop.departure_status == 'update'
        assert third_stop.departure == datetime.datetime(2012, 6, 16, 04, 04)
        assert third_stop.message is None

        fourth_stop = trip_update.stop_time_updates[4]
        assert fourth_stop.stop_id == 'StopR4'
        assert fourth_stop.arrival_status == 'update'
        assert fourth_stop.arrival_delay == timedelta(minutes=4)
        assert fourth_stop.arrival == datetime.datetime(2012, 6, 16, 04, 34)
        assert fourth_stop.departure_delay == timedelta(minutes=4)
        assert fourth_stop.departure_status == 'update'
        assert fourth_stop.departure == datetime.datetime(2012, 6, 16, 04, 34)
        assert fourth_stop.message is None


@pytest.fixture()
def partial_update_gtfs_rt_data_1():
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=15, hour=15))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-R-vj1"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    return feed

@pytest.fixture()
def partial_update_gtfs_rt_data_2():
    """
    This fixture is almost the same as partial_update_gtfs_rt_data_1
    Based on the previous one, we add one more stop_time_update
    """
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=15, hour=15))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-R-vj1"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 180
    stu.stop_sequence = 4
    stu.stop_id = "Code-StopR4"

    return feed

@pytest.fixture()
def partial_update_gtfs_rt_data_3():
    """
    This fixture is almost the same as partial_update_gtfs_rt_data_2
    We add a new trip_update
    """
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=15, hour=15))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-R-vj1"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 180
    stu.stop_sequence = 4
    stu.stop_id = "Code-StopR4"

    # Another trip update
    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-R-vj2"

    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 1
    stu.stop_id = "Code-StopR1"

    return feed

def test_gtfs_rt_partial_update_same_feed(partial_update_gtfs_rt_data_1):
    """
    In this test, we will send the same gtfs-rt twice, the second sending should not create neither
    new trip updates nor new stop time updates
    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=partial_update_gtfs_rt_data_1.SerializeToString())
    assert resp.status_code == 200

    def check(nb_rt_update):
        with app.app_context():
            assert len(RealTimeUpdate.query.all()) == nb_rt_update
            assert len(TripUpdate.query.all()) == 1
            assert len(StopTimeUpdate.query.all()) == 4

            trip_update = TripUpdate.query.first()
            assert trip_update.stop_time_updates[0].arrival_delay.seconds == 0
            assert trip_update.stop_time_updates[1].arrival_delay.seconds == 60
            assert trip_update.stop_time_updates[2].arrival_delay.seconds == 0
            assert trip_update.stop_time_updates[3].arrival_delay.seconds == 0
            # since the second real_time_update is the same as the first one,
            # the second one won't have an effect on existing trip update,
            # so the length is 1
            assert len(trip_update.real_time_updates) == 1

    check(nb_rt_update=1)

    # Now we apply exactly the same gtfs-rt, the new gtfs-rt will be save into the db,
    # which increment the nb of RealTimeUpdate, but the rest remains the same
    resp = tester.post('/gtfs_rt', data=partial_update_gtfs_rt_data_1.SerializeToString())
    assert resp.status_code == 200
    check(nb_rt_update=2)


def test_gtfs_rt_partial_update_diff_feed_1(partial_update_gtfs_rt_data_1,
                                            partial_update_gtfs_rt_data_2):
    """
    In this test, we will send the two different gtfs-rt
    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=partial_update_gtfs_rt_data_1.SerializeToString())
    assert resp.status_code == 200

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 4

        trip_update = TripUpdate.query.first()
        assert trip_update.stop_time_updates[0].arrival_delay.seconds == 0
        assert trip_update.stop_time_updates[1].arrival_delay.seconds == 60
        assert trip_update.stop_time_updates[2].arrival_delay.seconds == 0
        assert trip_update.stop_time_updates[3].arrival_delay.seconds == 0
        assert len(trip_update.real_time_updates) == 1

    # Now we apply another gtfs-rt, the new gtfs-rt will be save into the db and
    # increments the nb of real_time_updates
    resp = tester.post('/gtfs_rt', data=partial_update_gtfs_rt_data_2.SerializeToString())
    assert resp.status_code == 200
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 4

        trip_update = TripUpdate.query.first()
        assert trip_update.stop_time_updates[0].arrival_delay.seconds == 0
        assert trip_update.stop_time_updates[1].arrival_delay.seconds == 60
        assert trip_update.stop_time_updates[2].arrival_delay.seconds == 0
        assert trip_update.stop_time_updates[3].arrival_delay.seconds == 180
        assert len(trip_update.real_time_updates) == 2


def test_gtfs_rt_partial_update_diff_feed_2(partial_update_gtfs_rt_data_2,
                                            partial_update_gtfs_rt_data_3):
    """
    In this test, we send a gtfs-rt containing only one trip_update, the send a gtfs-rt
    containing two trip_updates
    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=partial_update_gtfs_rt_data_2.SerializeToString())
    assert resp.status_code == 200

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 4

        trip_update = TripUpdate.query.first()
        first_trip_update_db_id = trip_update.vj_id
        assert trip_update.stop_time_updates[0].arrival_delay.seconds == 0
        assert trip_update.stop_time_updates[1].arrival_delay.seconds == 60
        assert trip_update.stop_time_updates[2].arrival_delay.seconds == 0
        assert trip_update.stop_time_updates[3].arrival_delay.seconds == 180
        assert len(trip_update.real_time_updates) == 1

    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=partial_update_gtfs_rt_data_3.SerializeToString())
    assert resp.status_code == 200

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        trip_updates = TripUpdate.query.all()
        assert len(trip_updates) == 2
        assert len(StopTimeUpdate.query.all()) == 8

        for trip_update in trip_updates:
            if trip_update.vj_id == first_trip_update_db_id:
                assert trip_update.stop_time_updates[0].arrival_delay.seconds == 0
                assert trip_update.stop_time_updates[1].arrival_delay.seconds == 60
                assert trip_update.stop_time_updates[2].arrival_delay.seconds == 0
                assert trip_update.stop_time_updates[3].arrival_delay.seconds == 180
                assert len(trip_update.real_time_updates) == 1
            else:
                assert trip_update.stop_time_updates[0].arrival_delay.seconds == 60
                assert trip_update.stop_time_updates[1].arrival_delay.seconds == 0
                assert trip_update.stop_time_updates[2].arrival_delay.seconds == 0
                assert trip_update.stop_time_updates[3].arrival_delay.seconds == 0
                assert len(trip_update.real_time_updates) == 1


@pytest.fixture()
def lollypop_gtfs_rt_data():
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=15, hour=9))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-lollypop"

    #arrival and departure in vehiclejourney 100000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 1
    stu.stop_id = "Code-StopR1"

    #arrival and departure in vehiclejourney 101000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 120
    stu.departure.delay = 120
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    #arrival and departure in vehiclejourney 102000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 3
    stu.stop_id = "Code-StopR3"

    #arrival and departure in vehiclejourney 103000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 0
    stu.departure.delay = 0
    stu.stop_sequence = 4
    stu.stop_id = "Code-StopR2"

    #arrival and departure in vehiclejourney 104000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 0
    stu.departure.delay = 0
    stu.stop_sequence = 5
    stu.stop_id = "Code-StopR4"

    return feed


def test_gtfs_lollypop_model_builder(lollypop_gtfs_rt_data):
    """
    test the model builder with a lollypop gtfs-rt
    """
    with app.app_context():
        data = ''
        rt_update = RealTimeUpdate(data, connector='gtfs-rt', contributor='realtime.gtfs')
        trip_updates = gtfs_rt.KirinModelBuilder(dumb_nav_wrapper()).build(rt_update, lollypop_gtfs_rt_data)

        # we associate the trip_update manually for sqlalchemy to make the links
        rt_update.trip_updates = trip_updates
        db.session.add(rt_update)
        db.session.commit()

        assert len(trip_updates) == 1
        assert len(trip_updates[0].stop_time_updates) == 5

        first_stop = trip_updates[0].stop_time_updates[0]
        assert first_stop.stop_id == 'StopR1'
        assert first_stop.arrival_status == 'update'
        assert first_stop.arrival_delay == timedelta(minutes=1)
        assert first_stop.departure_status == 'update'
        assert first_stop.departure_delay == timedelta(minutes=1)
        assert first_stop.message is None

        second_stop = trip_updates[0].stop_time_updates[1]
        assert second_stop.stop_id == 'StopR2'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival_delay == timedelta(minutes=2)
        assert second_stop.departure_status == 'update'
        assert second_stop.departure_delay == timedelta(minutes=2)
        assert second_stop.message is None

        third_stop = trip_updates[0].stop_time_updates[2]
        assert third_stop.stop_id == 'StopR3'
        assert third_stop.arrival_status == 'update'
        assert third_stop.arrival_delay == timedelta(minutes=1)
        assert third_stop.departure_status == 'update'
        assert third_stop.departure_delay == timedelta(minutes=1)
        assert third_stop.message is None

        fourth_stop = trip_updates[0].stop_time_updates[3]
        assert fourth_stop.stop_id == 'StopR2'
        assert fourth_stop.arrival_status == 'none'
        assert fourth_stop.arrival_delay is None
        assert fourth_stop.departure_status == 'none'
        assert fourth_stop.departure_delay is None
        assert fourth_stop.message is None

        fifth_stop = trip_updates[0].stop_time_updates[4]
        assert fifth_stop.stop_id == 'StopR4'
        assert fifth_stop.arrival_status == 'none'
        assert fifth_stop.arrival_delay is None
        assert fifth_stop.departure_status == 'none'
        assert fifth_stop.departure_delay is None
        assert fifth_stop.message is None

        feed = convert_to_gtfsrt(trip_updates)
        assert feed.entity[0].trip_update.trip.start_date == u'20120615' #must be UTC start date

@pytest.fixture()
def bad_ordered_gtfs_rt_data():
    feed = gtfs_realtime_pb2.FeedMessage()

    feed.header.gtfs_realtime_version = "1.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = to_posix_time(datetime.datetime(year=2012, month=6, day=15, hour=9))

    entity = feed.entity.add()
    entity.id = 'bob'
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "Code-orders"

    #arrival and departure in vehiclejourney 101000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 2
    stu.stop_id = "Code-StopR2"

    #arrival and departure in vehiclejourney 102000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 120
    stu.departure.delay = 120
    stu.stop_sequence = 3
    stu.stop_id = "Code-StopR3"

    #stop absent in vehiclejourney and will be rejected
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 120
    stu.departure.delay = 120
    stu.stop_sequence = 4
    stu.stop_id = "Code-Stop-RT-1"

    #stop laso present in vehiclejourney but since it's order doesn't match with stop_sequence, will be rejected.
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 120
    stu.departure.delay = 120
    stu.stop_sequence = 5
    stu.stop_id = "Code-StopR4"

    #arrival and departure in vehiclejourney 105000
    stu = trip_update.stop_time_update.add()
    stu.arrival.delay = 60
    stu.departure.delay = 60
    stu.stop_sequence = 6
    stu.stop_id = "Code-StopR6"

    return feed


def test_gtfs_bad_order_model_builder(bad_ordered_gtfs_rt_data):
    """
    test the model builder with stops absent or not matching order in gtfs-rt
    """
    with app.app_context():
        data = ''
        rt_update = RealTimeUpdate(data, connector='gtfs-rt', contributor='realtime.gtfs')
        trip_updates = gtfs_rt.KirinModelBuilder(dumb_nav_wrapper()).build(rt_update, bad_ordered_gtfs_rt_data)

        # we associate the trip_update manually for sqlalchemy to make the links
        rt_update.trip_updates = trip_updates
        db.session.add(rt_update)
        db.session.commit()

        assert len(trip_updates) == 1
        assert len(trip_updates[0].stop_time_updates) == 3

        first_stop = trip_updates[0].stop_time_updates[0]
        assert first_stop.stop_id == 'StopR2'
        assert first_stop.arrival_status == 'update'
        assert first_stop.arrival_delay == timedelta(minutes=1)
        assert first_stop.departure_status == 'update'
        assert first_stop.departure_delay == timedelta(minutes=1)
        assert first_stop.message is None

        second_stop = trip_updates[0].stop_time_updates[1]
        assert second_stop.stop_id == 'StopR3'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival_delay == timedelta(minutes=2)
        assert second_stop.departure_status == 'update'
        assert second_stop.departure_delay == timedelta(minutes=2)
        assert second_stop.message is None

        third_stop = trip_updates[0].stop_time_updates[2]
        assert third_stop.stop_id == 'StopR6'
        assert third_stop.arrival_status == 'update'
        assert third_stop.arrival_delay == timedelta(minutes=1)
        assert third_stop.departure_status == 'update'
        assert third_stop.departure_delay == timedelta(minutes=1)
        assert third_stop.message is None


def test_gtfs_bad_order_model_builder_with_post(bad_ordered_gtfs_rt_data):
    """
    test the model builder with stops absent or not matching order in gtfs-rt

    we have realtime data with 6 stops and vehicle journey with 6 stops

    After the merge, we should have 3 stops properly delayed

    """
    tester = app.test_client()
    resp = tester.post('/gtfs_rt', data=bad_ordered_gtfs_rt_data.SerializeToString())
    assert resp.status_code == 200

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6

        trip_update = TripUpdate.find_by_dated_vj('R:vj1', datetime.datetime(2012, 6, 15, 14, 00))

        assert trip_update

        assert trip_update.vj.get_start_timestamp() == datetime.datetime(2012, 6, 15, 14, 00, tzinfo=utc)

        # navitia's time are in local, but we return UTC time, and the stop is in sherbrooke, so UTC-4h
        first_stop = trip_update.stop_time_updates[0]
        assert first_stop.stop_id == 'StopR1'
        assert first_stop.arrival_status == 'none'
        assert first_stop.arrival_delay == timedelta(0)
        # 23:30 in local + 4h to get it in UTC + 1minute of delay
        assert first_stop.arrival == datetime.datetime(2012, 6, 15, 14, 00)
        assert first_stop.departure_delay == timedelta(0)
        assert first_stop.departure_status == 'none'
        assert first_stop.departure == datetime.datetime(2012, 6, 15, 14, 00)
        assert first_stop.message is None

        second_stop = trip_update.stop_time_updates[1]
        assert second_stop.stop_id == 'StopR2'
        assert second_stop.arrival_status == 'update'
        assert second_stop.arrival == datetime.datetime(2012, 6, 15, 14, 11)
        assert second_stop.arrival_delay == timedelta(minutes=1)
        assert second_stop.departure == datetime.datetime(2012, 6, 15, 14, 11)
        assert second_stop.departure_delay == timedelta(minutes=1)
        assert second_stop.departure_status == 'update'
        assert second_stop.message is None

        third_stop = trip_update.stop_time_updates[2]
        assert third_stop.stop_id == 'StopR3'
        assert third_stop.arrival_status == 'update'
        assert third_stop.arrival == datetime.datetime(2012, 6, 15, 14, 22)
        assert third_stop.arrival_delay == timedelta(minutes=2)
        assert third_stop.departure == datetime.datetime(2012, 6, 15, 14, 22)
        assert third_stop.departure_delay == timedelta(minutes=2)
        assert third_stop.departure_status == 'update'
        assert third_stop.message is None

        fourth_stop = trip_update.stop_time_updates[3]
        assert fourth_stop.stop_id == 'StopR4'
        assert fourth_stop.arrival_status == 'none'
        assert fourth_stop.arrival_delay == timedelta(0)
        assert fourth_stop.arrival == datetime.datetime(2012, 6, 15, 14, 40)
        assert fourth_stop.departure_delay == timedelta(0)
        assert fourth_stop.departure_status == 'none'
        assert fourth_stop.departure == datetime.datetime(2012, 6, 15, 14, 40)
        assert fourth_stop.message is None

        fifth_stop = trip_update.stop_time_updates[4]
        assert fifth_stop.stop_id == 'StopR5'
        assert fifth_stop.arrival_status == 'none'
        assert fifth_stop.arrival_delay == timedelta(0)
        assert fifth_stop.arrival == datetime.datetime(2012, 6, 15, 14, 40)
        assert fifth_stop.departure_delay == timedelta(0)
        assert fifth_stop.departure_status == 'none'
        assert fifth_stop.departure == datetime.datetime(2012, 6, 15, 14, 40)
        assert fifth_stop.message is None

        sixth_stop = trip_update.stop_time_updates[5]
        assert sixth_stop.stop_id == 'StopR6'
        assert sixth_stop.arrival_status == 'update'
        assert sixth_stop.arrival_delay == timedelta(minutes=1)
        assert sixth_stop.arrival == datetime.datetime(2012, 6, 15, 14, 51)
        assert sixth_stop.departure_delay == timedelta(minutes=1)
        assert sixth_stop.departure_status == 'update'
        assert sixth_stop.departure == datetime.datetime(2012, 6, 15, 14, 51)
        assert sixth_stop.message is None