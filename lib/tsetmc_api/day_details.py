import json
import math
import re

import requests
from bs4 import BeautifulSoup

_intraday_pattern = re.compile("var (?P<name>.*)=(?P<content>\[[^;]*\]);")


def _load_script_vars(asset_id, year, month, day):
    d = f'{year:04}{month:02}{day:02}'
    script_variables = {}

    # get data from url
    daily_content = requests.get(f'http://cdn.tsetmc.com/Loader.aspx?ParTree=15131P&i={asset_id}&d={d}').text

    # find and get script tags
    all_scripts = BeautifulSoup(daily_content, 'lxml').find_all('script')
    first_script = str(all_scripts[-3])
    second_script = str(all_scripts[-2])
    third_script = str(all_scripts[-1])

    # extract first script variables
    matches = _intraday_pattern.findall(first_script)
    for match in matches:
        script_variables[match[0]] = json.loads(match[1].replace('\'', '"'))

    # extract second script variables
    matches = _intraday_pattern.findall(second_script)
    for match in matches:
        script_variables[match[0]] = json.loads(match[1].replace('\'', '"'))

    # extract third script variables
    matches = _intraday_pattern.findall(third_script)
    for match in matches:
        script_variables[match[0]] = json.loads(match[1].replace('\'', '"'))

    return script_variables


def _extract_asset_details_data(script_vars):
    asset_details = {
        'full_name': script_vars['InstSimpleData'][0],
        'short_name': script_vars['InstSimpleData'][1],
        'market_short_name': script_vars['InstSimpleData'][2],
        'market_full_name': script_vars['InstSimpleData'][3],
        'isin': script_vars['InstSimpleData'][7],
        'shares_count': script_vars['InstSimpleData'][8],
        'base_volume': script_vars['InstSimpleData'][9],
    }

    return asset_details


def _extract_price_data(script_vars):
    # State Data
    state_data = []
    for isd in script_vars['InstrumentStateData']:
        state_data.append(isd[1:])

    last_state_i = 0
    next_state_change_time = state_data[0][0] if len(state_data) > 1 else math.inf
    last_state = state_data[0][1] if len(state_data) > 1 else 'A '  # todo: convert to enum

    # Price Data
    price_data = []
    for cpd in script_vars['ClosingPriceData']:
        continuous_time = int(cpd[12])
        while continuous_time >= next_state_change_time:
            if len(state_data) > (last_state_i + 1):
                last_state_i += 1
                next_state_change_time = state_data[last_state_i][0]
                last_state = state_data[last_state_i][1]  # todo: convert to enum
            else:
                next_state_change_time = math.inf

        price_data.append({
            't': continuous_time,
            'lst': int(cpd[2]),
            'y': int(cpd[5]),
            'o': int(cpd[4]),
            'c': int(cpd[3]),
            'h': int(cpd[6]),
            'l': int(cpd[7]),
            'cnt': int(cpd[8]),
            'v': int(cpd[9]),
            's': last_state,
        })

    price_data.sort(key=lambda x: x['t'])
    return price_data


def _extract_orders_data(script_vars):
    orders = []
    for bld in script_vars['BestLimitData']:
        order = {
            't': bld[0],
            'rank': int(bld[1]),
            'bcnt': int(bld[2]),
            'bv': int(bld[3]),
            'bp': int(bld[4]),
            'scnt': int(bld[7]),
            'sv': int(bld[6]),
            'sp': int(bld[5]),
        }

        orders.append(order)

    orders.sort(key=lambda x: x['t'])
    return orders


def _extract_trade_data(script_vars):
    trades = [
        {
            'order': int(x[0]),
            't': int(f'{x[1][:2]}{x[1][3:5]}{x[1][6:]}'),
            'v': int(x[2]),
            'p': int(x[3]),
        }
        for x in script_vars['IntraTradeData']
    ]

    return trades


def _extract_share_holders_data(script_vars):
    share_holders = []
    for shd in script_vars['ShareHolderData']:
        share_holders.append({
            'id': shd[0],
            'shares_count': shd[2],
            'shares_percent': shd[3],
            'name': shd[5],
        })

    yesterday_share_holders = []
    for shd in script_vars['ShareHolderDataYesterday']:
        yesterday_share_holders.append({
            'id': shd[0],
            'shares_count': shd[2],
            'shares_percent': shd[3],
            'name': shd[5],
        })

    return yesterday_share_holders, share_holders


def _create_snapshot(price_data, orders_data, distinct=True):
    max_t = price_data['t']
    snapshot = {
        't': max_t,
        'lst': price_data['lst'],
        'y': price_data['y'],
        'o': price_data['o'],
        'c': price_data['c'],
        'l': price_data['l'],
        'cnt': price_data['cnt'],
        'v': price_data['v'],
        's': price_data['s'],
    } if distinct else price_data

    buy_orders = []
    sell_orders = []
    for to in orders_data:
        if to is not None:
            buy_orders.append({
                't': to['t'],
                'cnt': to['bcnt'],
                'v': to['bv'],
                'p': to['bp'],
            })
            sell_orders.append({
                't': to['t'],
                'cnt': to['scnt'],
                'vol': to['sv'],
                'p': to['sp'],
            })

            max_t = max(max_t, to['t'])

    snapshot['t'] = max_t
    snapshot['buy'] = buy_orders
    snapshot['sell'] = sell_orders

    return snapshot


class AssetDayDetails:
    def __init__(self, asset_id, year, month, day):
        """
        :param asset_id:
        :param year: gregorian year
        :param month: gregorian month
        :param day: gregorian day
        """
        self.asset_id = asset_id
        self.year = year
        self.month = month
        self.day = day

        self._load_raw_data()

    def _load_raw_data(self):
        script_vars = _load_script_vars(self.asset_id, self.year, self.month, self.day)

        self._asset_details = _extract_asset_details_data(script_vars)
        self._price_data = _extract_price_data(script_vars)

        if not self._price_data:
            raise Exception('there is no data for this asset in the specified date')

        self._yesterday_shareholders, self._shareholders = _extract_share_holders_data(script_vars)
        self._trades = _extract_trade_data(script_vars)
        self._orders_data = _extract_orders_data(script_vars)

    def get_asset_details(self):
        """
        returns asset details in that day
        currently contains: fullname, shortname, isin, market full & short names, total shares count and base volume
        :return: { full_name, short_name, isin, market_full_name, market_short_name, shares_count, base_volume }
        :rtype: object
        """
        return self._asset_details

    def get_shareholders(self):
        """
        gets shareholders after this day was passed
        :return: [{ shareholder_id, shares_count, shares_percent, shareholder_name }]
        """
        return self._shareholders

    def get_yesterday_shareholders(self):
        """
        gets shareholders the day before
        :return: [{ shareholder_id, shares_count, shares_percent, shareholder_name }]
        """
        return self._yesterday_shareholders

    def get_trades(self):
        """
        returns list of trades resided in this day
        :return: list of trades
        """
        return self._trades

    def get_all_snapshots_at(self, hour, minute, seconds):
        """
        returns list of asset snapshots in the specified time or the single last tick before it (if there is none in
        that exact time)
        :param hour:
        :param minute:
        :param seconds:
        :return: list of snapshots
        """

        # generate time value
        t = int(f'{hour}{minute:02}{seconds:02}')

        # find the price data of that time
        t_price_data = []
        prev_price_data = None
        for pd in self._price_data:
            if pd['t'] == t:
                t_price_data.append(pd)
            elif pd['t'] > t:
                if not t_price_data:
                    t_price_data.append(prev_price_data)
                break

            prev_price_data = pd
        else:
            if not t_price_data:
                t_price_data.append(self._price_data[-1])

        # find the orders
        t_orders = [None, None, None]
        prev_orders_data = [None, None, None]
        for od in self._orders_data:
            rank = od['rank'] - 1
            if t_orders[rank] is None:
                if od['t'] > t:
                    t_orders[rank] = prev_orders_data[rank]
                    if None not in t_orders:
                        break
                    continue
                prev_orders_data[rank] = od

        buy_orders = []
        sell_orders = []
        for to in t_orders:
            if to is not None:
                buy_orders.append({
                    't': to['t'],
                    'cnt': to['bcnt'],
                    'v': to['bv'],
                    'p': to['bp'],
                })
                sell_orders.append({
                    't': to['t'],
                    'cnt': to['scnt'],
                    'v': to['sv'],
                    'p': to['sp'],
                })

        # generate detailed ticks
        for tp in t_price_data:
            tp['buy'] = buy_orders
            tp['sell'] = sell_orders

        return t_price_data

    def get_snapshot_at(self, hour, minute, seconds):
        """
        returns single(last) snapshot of asset at the specified time of the day
        :param hour:
        :param minute:
        :param seconds:
        :return: single snapshot
        """
        return self.get_all_snapshots_at(hour, minute, seconds)[-1]

    def get_all_snapshots(self):
        max_pdi = len(self._price_data)
        max_odi = len(self._orders_data)
        pdi = 0
        odi = -1
        last_price_data = self._price_data[pdi]
        last_orders_data = [None, None, None]

        while True:
            phase = -1
            if pdi + 1 < max_pdi and odi + 1 < max_odi:
                if self._price_data[pdi + 1]['t'] < self._orders_data[odi + 1]['t']:
                    phase = 1
                elif self._price_data[pdi + 1]['t'] > self._orders_data[odi + 1]['t']:
                    phase = 2
                else:
                    phase = 3
            else:
                if pdi + 1 == max_pdi and odi + 1 == max_odi:
                    break
                elif pdi + 1 < max_pdi:
                    phase = 1
                elif odi + 1 < max_odi:
                    phase = 2

            # step price data and use the old order data
            if phase == 1 or phase == 3:
                pdi += 1
                last_price_data = self._price_data[pdi]

            # step orders
            if phase == 2 or phase == 3:
                t = self._orders_data[odi + 1]['t']
                orders_found = [None, None, None]
                while True:
                    if odi + 1 >= max_odi:
                        break

                    order = self._orders_data[odi + 1]
                    rank = order['rank'] - 1
                    if orders_found[rank] is not None or order['t'] > t:
                        break

                    odi += 1
                    orders_found[rank] = order
                    last_orders_data[rank] = order

            yield _create_snapshot(last_price_data, last_orders_data)

    def pick_snapshots_at(self, pick_times):
        ll = []
        pick_times.sort()
        pti = 0
        all_snapshots = self.get_all_snapshots()

        prev_snapshot = None
        for snapshot in all_snapshots:
            if snapshot['t'] > pick_times[pti]:
                pti += 1

                if prev_snapshot is not None:
                    ll.append(prev_snapshot)

                if pti == len(pick_times):
                    break

            prev_snapshot = snapshot.copy()

        return ll
