def register(app):
    from mcp import routes

    app.register_blueprint(routes.bp)
