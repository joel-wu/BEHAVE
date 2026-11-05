"""Owner-to-owner input transfer shared by Syn entry points."""

from behave.core import runtime_defaults as defaults
from behave.core.storage import publication_map


class ReadSet:
    """Record consumed references in first-read order, without caching payloads.

    Validation stays with the content reader and its callers. An explicit
    owner-transfer callback may retain these inputs without reading their bytes
    again; arbitrary readers retain normal verified publication.
    """

    def __init__(self, reader):
        self.reader, self.references = reader, {}

    def read(self, reference):
        payload = self.reader.read(reference)
        self.references[reference] = None
        return payload

    def publish(self, destination, *, transfer=None):
        references = tuple(self.references)
        if transfer is None:
            destination.publish(self.reader, references)
        else:
            transfer(destination, references)


class Inputs:
    """Resolve explicit references in frozen stores, without listing their contents.

    Earlier stores win. Cache only referenced owners, not every historical blob;
    each reader still verifies the payload when it is read.
    """

    def __init__(self, *stores, workers=defaults.PUBLICATION_WORKERS):
        self.stores = stores
        self.workers = workers
        self.owners = {}

    def owner(self, reference):
        if reference.sha256 not in self.owners:
            for store in self.stores:
                if (store.directory / reference.sha256).is_file():
                    self.owners[reference.sha256] = store
                    break
            else:
                raise FileNotFoundError(f"no input store owns {reference.sha256}")
        return self.owners[reference.sha256]

    def read(self, reference):
        return self.owner(reference).read(reference)

    def publish(self, target, references):
        grouped = {}
        for reference, owner in publication_map(lambda ref: (ref, self.owner(ref)),
                                                 references, workers=self.workers):
            grouped.setdefault(owner, []).append(reference)
        for store, refs in grouped.items():
            target.publish_owned(store, tuple(refs), workers=self.workers)
