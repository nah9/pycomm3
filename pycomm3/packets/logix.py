# -*- coding: utf-8 -*-
#
# Copyright (c) 2020 Ian Ottoway <ian@ottoway.dev>
# Copyright (c) 2014 Agostino Ruscito <ruscito@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

import logging
from itertools import tee, zip_longest
from reprlib import repr as _r
from typing import Dict, Any, Sequence, Union

from .ethernetip import SendUnitDataRequestPacket, SendUnitDataResponsePacket
from .util import parse_read_reply, request_path, tag_request_path
from ..bytes_ import Pack, Unpack
from ..cip import DataType, ClassCode, Services, DataTypeSize
from ..const import STRUCTURE_READ_REPLY
from ..exceptions import RequestError


class TagServiceResponsePacket(SendUnitDataResponsePacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')

    def __init__(self, request: 'TagServiceRequestPacket', raw_data: bytes = None):
        self.tag = request.tag
        self.elements = request.elements
        self.tag_info = request.tag_info
        super().__init__(request, raw_data)


class TagServiceRequestPacket(SendUnitDataRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    response_class = TagServiceResponsePacket
    tag_service = None

    def __init__(self, tag: str, elements: int, tag_info: Dict[str, Any],
                 request_id: int, use_instance_id: bool = True):
        super().__init__()
        self.tag = tag
        self.elements = elements
        self.tag_info = tag_info
        self.request_id = request_id
        self._use_instance_id = use_instance_id
        self.request_path = None

    def tag_only_message(self):
        return b''.join((self.tag_service, self.request_path, Pack.uint(self.elements)))


class ReadTagResponsePacket(TagServiceResponsePacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')

    def __init__(self, request: 'ReadTagRequestPacket', raw_data: bytes = None):
        self.value = None
        self.data_type = None
        super().__init__(request, raw_data)

    def _parse_reply(self, dont_parse=False):
        try:
            super()._parse_reply()
            if self.is_valid() and not dont_parse:
                self.value, self.data_type = parse_read_reply(self.data, self.tag_info, self.elements)
        except Exception as err:
            self.__log.exception('Failed parsing reply data')
            self.value = None
            self._error = f'Failed to parse reply - {err}'

    def __repr__(self):
        return f'{self.__class__.__name__}({self.data_type!r}, {_r(self.value)}, {self.service_status!r})'


# TODO: remove the request_path arg, the path should be created in the request
#       it was originally, but then moved outside to make packet size estimation more accurate
#       but, multi packet will be changed to accept packets and can then use the full message
#       from the tag packet for tracking size

class ReadTagRequestPacket(TagServiceRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    type_ = 'read'
    response_class = ReadTagResponsePacket
    tag_service = Services.read_tag

    def _setup_message(self):
        super()._setup_message()
        if self.request_path is None:
            self.request_path = tag_request_path(self.tag, self.tag_info, self._use_instance_id)
            if self.request_path is None:
                self.error = f'Failed to build request path for tag'
        self._msg.append(self.tag_only_message())



class ReadTagFragmentedResponsePacket(ReadTagResponsePacket):
    # TODO
    __log = logging.getLogger(f'{__module__}.{__qualname__}')

    def __init__(self, request: 'ReadTagFragmentedRequestPacket', raw_data: bytes = None):
        self.value = None
        self._data_type = None
        self.value_bytes = None

        super().__init__(request, raw_data)

    def _parse_reply(self):
        super()._parse_reply(dont_parse = True)
        if self.data[:2] == STRUCTURE_READ_REPLY:
            self.value_bytes = self.data[4:]
            self._data_type = self.data[:4]
        else:
            self.value_bytes = self.data[2:]
            self._data_type = self.data[:2]

    def parse_value(self):
        try:
            if self.is_valid():
                self.value, self.data_type = parse_read_reply(self._data_type + self.value_bytes,
                                                              self.request.tag_info, self.request.elements)
            else:
                self.value, self.data_type = None, None
        except Exception as err:
            self.__log.exception('Failed parsing reply data')
            self.value = None
            self._error = f'Failed to parse reply - {err}'

    def __repr__(self):
        return f'{self.__class__.__name__}(raw_data={_r(self.raw)})'

    __str__ = __repr__


class ReadTagFragmentedRequestPacket(ReadTagRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    type_ = 'read'
    response_class = ReadTagFragmentedResponsePacket
    tag_service = Services.read_tag_fragmented

    def __init__(self, tag: str, elements: int, tag_info: Dict[str, Any],
                 request_id: int, use_instance_id: bool = True, offset: int = 0):
        super().__init__(tag, elements, tag_info, request_id, use_instance_id)
        self.offset = offset

    def _setup_message(self):
        super()._setup_message()
        self._msg.append(Pack.udint(self.offset))

    @classmethod
    def from_request(cls, request: Union[ReadTagRequestPacket, 'ReadTagFragmentedRequestPacket'], offset=0) -> 'ReadTagFragmentedRequestPacket':
        new_request = cls(
            request.tag,
            request.elements,
            request.tag_info,
            request.request_id,
            request._use_instance_id,
            offset
        )
        new_request.request_path = request.request_path

        return new_request

    def __repr__(self):
        return f'{self.__class__.__name__}(tag={self.tag!r}, elements={self.elements!r})'


class WriteTagResponsePacket(TagServiceResponsePacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')

    def __init__(self, request: 'WriteTagRequestPacket', raw_data: bytes = None):
        self.value = request.value
        self.data_type = request.data_type
        super().__init__(request, raw_data)


class WriteTagRequestPacket(TagServiceRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    type_ = 'write'
    response_class = WriteTagResponsePacket
    tag_service = Services.write_tag

    def __init__(self, tag: str, elements: int, tag_info: Dict[str, Any], request_id: int,
                 use_instance_id: bool = True, value: bytes = b''):
        super().__init__(tag, elements, tag_info, request_id, use_instance_id)
        self.value = value
        self.data_type = tag_info['data_type_name']
        self._packed_data_type = None

        if tag_info['tag_type'] == 'struct':
            if not isinstance(value, bytes):
                raise RequestError('Writing UDTs only supports bytes for value')
            self._packed_data_type = b'\xA0\x02' + Pack.uint(tag_info['data_type']['template']['structure_handle'])

        elif self.data_type not in DataType:
            raise RequestError(f"Unsupported data type: {self.data_type!r}")
        else:
            self._packed_data_type = Pack.uint(DataType[self.data_type])

    def _setup_message(self):
        super()._setup_message()
        if self.request_path is None:
            self.request_path = tag_request_path(self.tag, self.tag_info, self._use_instance_id)
            if self.request_path is None:
                self.error = f'Failed to build request path for tag'
        self._msg += [self.tag_service, self.request_path, self._packed_data_type,
                      Pack.uint(self.elements), self.value]

    def tag_only_message(self):
        return b''.join((self.tag_service, self.request_path, self._packed_data_type,
                         Pack.uint(self.elements), self.value))

    def __repr__(self):
        return f'{self.__class__.__name__}(tag={self.tag!r}, value={_r(self.value)}, elements={self.elements!r})'


class WriteTagFragmentedResponsePacket(WriteTagResponsePacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')


class WriteTagFragmentedRequestPacket(WriteTagRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    type_ = 'write'
    response_class = WriteTagFragmentedResponsePacket
    tag_service = Services.write_tag_fragmented

    def __init__(self, tag: str, elements: int, tag_info: Dict[str, Any],
                 request_id: int, use_instance_id: bool = True, offset: int = 0, value: bytes = b''):
        super().__init__(tag, elements, tag_info, request_id, use_instance_id)
        self.offset = offset
        self.value = value

    def _setup_message(self):
        super()._setup_message()
        self._msg.insert((len(self._msg) - 1), Pack.udint(self.offset))  # offset needs to go before value

    @classmethod
    def from_request(cls, request: WriteTagRequestPacket, offset: int = 0, value: bytes = b'') -> 'WriteTagFragmentedRequestPacket':
        new_request = cls(
            request.tag,
            request.elements,
            request.tag_info,
            request.request_id,
            request._use_instance_id,
            offset,
            value or request.value,
        )

        new_request.request_path = request.request_path

        return new_request


class ReadModifyWriteResponsePacket(WriteTagResponsePacket):
    ...


class ReadModifyWriteRequestPacket(SendUnitDataRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    type_ = 'write'
    response_class = ReadModifyWriteResponsePacket
    tag_service = Services.read_modify_write

    def __init__(self, tag: str, tag_info: Dict[str, Any], request_id: int, use_instance_id: bool = True,):
        super().__init__()
        self.tag = tag
        self.value = None
        self.elements = 0
        self.tag_info = tag_info
        self.request_id = request_id
        self._use_instance_id = use_instance_id
        self.data_type = tag_info['data_type_name']
        self.request_path = tag_request_path(tag, tag_info, use_instance_id)
        self.bits = []
        self._request_ids = []
        self._and_mask = 0xFFFF_FFFF_FFFF_FFFF
        self._or_mask = 0x0000_0000_0000_0000
        self._mask_size = DataTypeSize.get(self.data_type)

        if self._mask_size is None:
            raise RequestError(f'Invalid data type {tag_info["data_type"]} for writing bits')

        if self.request_path is None:
            self.error = 'Failed to create request path for tag'

    def set_bit(self, bit: int, value: bool, request_id: int):
        if self.data_type == 'DWORD':
            bit = bit % 32

        if value:
            self._or_mask |= (1 << bit)
            self._and_mask |= (1 << bit)
        else:
            self._or_mask &= ~(1 << bit)
            self._and_mask &= ~(1 << bit)

        self.bits.append(bit)
        self._request_ids.append(request_id)

    def _setup_message(self):
        super()._setup_message()
        self._msg += [
            self.tag_service,
            self.request_path,
            Pack.uint(self._mask_size),
            Pack.ulint(self._or_mask)[:self._mask_size],
            Pack.ulint(self._and_mask)[:self._and_mask]
        ]


class MultiServiceResponsePacket(SendUnitDataResponsePacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')

    def __init__(self, request: 'MultiServiceRequestPacket', raw_data: bytes = None):
        self.request = request
        self.values = None
        self.request_statuses = None
        self.responses = []
        super().__init__(request, raw_data)

    def _parse_reply(self):
        super()._parse_reply()
        num_replies = Unpack.uint(self.data)
        offset_data = self.data[2:2 + 2 * num_replies]
        offsets = (Unpack.uint(offset_data[i:i+2]) for i in range(0, len(offset_data), 2))
        start, end = tee(offsets)  # split offsets into start/end indexes
        next(end)   # advance end by 1 so 2nd item is the end index for the first item
        reply_data = [self.data[i:j] for i, j in zip_longest(start, end)]

        padding = bytes(46)   # pad the front of the packet so it matches the size of
                              # a read tag response, probably not the best idea but it works for now

        for data, request in zip(reply_data, self.request.requests):
            response = request.response_class(request, padding + data)
            self.responses.append(response)

    def __repr__(self):
        return f'{self.__class__.__name__}(values={_r(self.values)}, error={self.error!r})'


class MultiServiceRequestPacket(SendUnitDataRequestPacket):
    __log = logging.getLogger(f'{__module__}.{__qualname__}')
    type_ = 'multi'
    response_class = MultiServiceResponsePacket

    def __init__(self, requests: Sequence[TagServiceRequestPacket]):
        super().__init__()
        self.requests = requests
        self.request_path = request_path(ClassCode.message_router, 1)

    def _setup_message(self):
        super()._setup_message()
        self._msg += [Services.multiple_service_request, self.request_path]

    def build_message(self):
        super().build_message()
        num_requests = len(self.requests)
        self._msg.append(Pack.uint(num_requests))
        offset = 2 + (num_requests * 2)
        offsets = []
        messages = []
        for request in self.requests:
            messages.append(request.tag_only_message())

        for msg in messages:
            offsets.append(Pack.uint(offset))
            offset += len(msg)

        return b''.join(self._msg + offsets + messages)
