# Plugins

Put each Python plugin in its own folder here: `plugins/<key>/plugin.py` defining `plugin = Plugin(...)`.
The folder name is the plugin's key. Restart Control Tower after adding or changing one, then switch it on in
Admin → Plugins.

How to write one: [docs/PLUGINS.md](../docs/PLUGINS.md). A working example: [examples/plugins/hello_shop](../examples/plugins/hello_shop).

A project in another language, or on another server, does not go here: connect it over HTTP in Admin → Plugins.
