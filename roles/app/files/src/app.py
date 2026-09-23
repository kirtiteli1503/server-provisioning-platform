"""Minimal Flask + Redis visit counter.

Swap this file (and requirements.txt / Dockerfile) for your own app -
the docker-compose.yml and the deploy tasks don't need to change as
long as the service is still called "web" and still listens on 5000.
"""
from flask import Flask, jsonify
import redis
import os

app = Flask(__name__)
r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), port=6379, decode_responses=True)


@app.route("/")
def index():
    count = r.incr("visits")
    return jsonify(message="Hello from the automated provisioning platform!", visits=count)


@app.route("/healthz")
def healthz():
    r.ping()
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
