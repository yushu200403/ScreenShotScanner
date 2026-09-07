def post_worker_init(worker):
    from app.lifecycle import start_runtime
    start_runtime(worker.wsgi)
