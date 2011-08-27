import asyncore, asynchat
import os, socket, string, sys
import StringIO, mimetools
import logging

# setup logging
logging.basicConfig(level=logging.DEBUG)
log = logging

READ_SIZE = 1024

class Consumer(asynchat.async_chat):
    def __init__(self, server, sock, addr, feeder):
        log.debug('setting up consumer')
        asynchat.async_chat.__init__(self, sock)

        self.server = server
        self.feeder = feeder
        self.set_terminator("\r\n\r\n")
        self.header = None
        self.data = ""
        self.shutdown = 0

        # add ourself as a consumer to the feeder
        self.feeder.add_consumer(self)

    def collect_incoming_data(self, data):
        log.debug('Consumer: collecting incoming data')
        self.data = self.data + data
        if len(self.data) > 16384:
            # limit the header size to prevent attacks
            self.shutdown = 1

    def found_terminator(self):
        if not self.header:
            # parse http header
            fp = StringIO.StringIO(self.data)
            request = string.split(fp.readline(), None, 2)
            if len(request) != 3:
                # badly formed request; just shut down
                self.shutdown = 1
            else:
                # parse message header
                self.header = mimetools.Message(fp)
                self.set_terminator("\r\n")
                log.debug('Consumer sending back headers')
                self.pushstatus(200, "OK")
                self.push('Content-type: application/octet-stream')
                self.push("\r\n\r\n")
            self.data = ""
        else:
            pass # ignore body data, for now

    def pushstatus(self, status, explanation="OK"):
        self.push("HTTP/1.0 %d %s\r\n" % (status, explanation))

    def write(self, data):
        log.debug('Consumer writing data: %s' % len(data))
        self.push(data)

    def handle_close(self):
        # remove ourself from the feeder
        # ... for some reason we are never in the list
        log.info('Consumer closed')
        self.feeder.remove_consumer(self)
        self.close()


class Feeder(asyncore.dispatcher):
    def __init__(self, host, port, path):
        log.debug('Feeder initializing')
        asyncore.dispatcher.__init__(self)

        # list of consumers hungrily awaiting data
        self.consumers = []

        # open a socket to make our request
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)

        # connect to our host
        self.connect( (host, port) )

        # setup our request header
        self.buffer = 'GET %s HTTP/1.0\r\n\r\n' % path

    def handle_read(self):
        log.debug('Feeder reading: %s' % READ_SIZE)
        # read in the next chunk
        data = self.recv(READ_SIZE)

        # send to the waiting consumers
        self.push_to_consumers(data)

    def push_to_consumers(self,data):
        # we've recieved some new data, push
        # it to the consumers
        log.debug('Feeder pushing: %s' % len(data))
        for consumer in self.consumers:
            consumer.write(data)

    def add_consumer(self,consumer):
        log.debug('Feeder: adding consumer')
        self.consumers.append(consumer)

    def remove_consumer(self,consumer):
        log.debug('Feeder: removing consumer')
        if consumer in self.consumers:
            self.consumers.remove(consumer)
        else:
            log.warning('Cant remove consumer')

    def handle_close(self):
        log.debug('Feeder stream closed')

        # if they close we close
        self.close()

        # close our consumers
        for consumer in self.consumers:
            consumer.close()

    def writable(self):
        return (len(self.buffer) > 0)

    def handle_write(self):
        sent = self.send(self.buffer)
        self.buffer = self.buffer[sent:]


class HTTPServer(asyncore.dispatcher):

    def __init__(self, port, feeder):
        # straitup listen for connections
        asyncore.dispatcher.__init__(self)
        if not port:
            port = 80
        self.port = port
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bind(("", port))
        self.listen(5)

    def handle_accept(self):
        # setup the consumer
        log.info('Server: Handling accept')
        conn, addr = self.accept()
        Consumer(self, conn, addr, feeder)


if __name__ == '__main__':
    SERVER_PORT, FEEDER_HOST, FEEDER_PORT, FEEDER_PATH = tuple(sys.argv[1:])

    # feed on some HTTP stream
    log.info('Setting up feeder: %s %s' % (FEEDER_HOST, FEEDER_PATH))
    feeder = Feeder(FEEDER_HOST, int(FEEDER_PORT), FEEDER_PATH)

    # wait for other ppl who want to feed
    log.info('Setting up server on: %s' % SERVER_PORT)
    server = HTTPServer(int(SERVER_PORT), feeder)

    # let it begin
    log.info('Starting loop')
    try:
        asyncore.loop()
    except KeyboardInterrupt:
        log.debug('Keyboard kill')
        server.close()
