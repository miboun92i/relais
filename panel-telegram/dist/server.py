import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

port = int(os.getenv('PORT', '8080'))
server = ThreadingHTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
print(f'Panel web disponible sur 0.0.0.0:{port}', flush=True)
server.serve_forever()
