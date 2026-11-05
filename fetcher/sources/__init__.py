"""One module per external source. Each subclasses `base.Source`, returns typed
records and never raises for a single bad item: it skips the item and reports it."""
