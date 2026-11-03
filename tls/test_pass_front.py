import gzip
import http.client
import importlib.util
from pathlib import Path
import socket
import ssl
import sys
import threading
import unittest
from unittest.mock import patch


with patch.object(sys, 'argv', ['pass_front.py']):
    spec = importlib.util.spec_from_file_location('pass_front', Path(__file__).with_name('pass_front.py'))
    front = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(front)


def parameter(name, value, kind='dateTime'):
    return (f'<ParameterValueStruct><Name>{name}</Name>'
            f'<Value xsi:type="xsd:{kind}">{value}</Value></ParameterValueStruct>').encode()


class DateCompatibilityTest(unittest.TestCase):
    def test_forwarded_date_and_http_length(self):
        for encoding in ('', 'gzip'):
            with self.subTest(encoding=encoding):
                original = b'<cwmp:SetParameterValues>' + b''.join([
                    parameter('Device.JioSTB.USB.DebugValidityDate', '2027-01-01T00:00:00.000Z'),
                    parameter('Device.JioSTB.FOTA.DowngradeValidityDate', '2037-12-31T23:59:59Z'),
                    parameter('Device.ManagementServer.PeriodicInformTime', '2027-01-01T00:00:00.000Z'),
                    parameter('Device.JioSTB.Note', '2027-01-01T00:00:00.000Z', 'string'),
                    parameter('Device.JioSTB.OtherDate', '2027-01-01T00:00:00.123Z'),
                ]) + b'</cwmp:SetParameterValues>'
                expected = original.replace(b'2027-01-01T00:00:00.000Z', b'2027-01-01T00:00:00+00:00', 1)
                expected = expected.replace(b'2037-12-31T23:59:59Z', b'2037-12-31T23:59:59+00:00')
                self.check_response(original, expected, encoding)

    def test_readback_is_unchanged(self):
        body = (b'<cwmp:GetParameterValuesResponse>' +
                parameter('Device.JioSTB.USB.DebugValidityDate', '2027-01-01T00:00:00.000Z') +
                b'</cwmp:GetParameterValuesResponse>')
        self.check_response(body, body, '')

    def check_response(self, body, expected, encoding):
        payload = gzip.compress(body) if encoding else body
        headers = [('Content-Type', 'text/xml'), ('Content-Length', str(len(payload)))]
        if encoding:
            headers.append(('Content-Encoding', encoding))

        class Response:
            status, reason = 200, 'OK'
            def read(self): return payload
            def getheaders(self): return headers
            def getheader(self, name): return dict(headers).get(name.title())

        class Upstream:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass

        client, server = socket.socketpair()
        client.settimeout(5)
        server.settimeout(5)
        with patch.object(front.http.client, 'HTTPConnection', Upstream), patch.object(front, 'log'):
            thread = threading.Thread(target=front.handle, args=(server, ('127.0.0.1', 0)), daemon=True)
            thread.start()
            with ssl._create_unverified_context().wrap_socket(client, server_hostname='localhost') as tls:
                tls.sendall(b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n')
                response = http.client.HTTPResponse(tls)
                response.begin()
                actual = response.read()
                self.assertEqual(int(response.getheader('Content-Length')), len(actual))
                self.assertEqual(response.getheader('Content-Encoding'), encoding or None)
                self.assertEqual(gzip.decompress(actual) if encoding else actual, expected)
                response.close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
