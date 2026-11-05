"""Raw next-token targets are source-only and counted exactly once."""

from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.train.training_data import (
    RawCorpus,
    RawEncoding,
    RawSegment,
    load_raw_corpus,
    native_train_data,
    segment_document,
    tokenize_materials,
)


def encoding(context=4):
    return RawEncoding(BlobRef.of(b"tokenizer"), "tokenizers/fixture", 1000, 1001, context)


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 5, 64, 257])
@pytest.mark.parametrize("context", [2, 3, 4, 128])
def test_chunking_predicts_every_source_token_and_eos_once(length, context):
    document = FileRef("source/code.txt", BlobRef.of(b"original"))
    tokens = tuple(range(length))
    segments = segment_document(document, tokens, encoding(context))
    assert tuple(token for segment in segments for token in segment.tokens[1:]) == (*tokens, 1001)
    assert sum(segment.targets for segment in segments) == length + 1
    assert all(2 <= len(segment.tokens) <= context for segment in segments)
    assert all(a.tokens[-1] == b.tokens[0] for a, b in zip(segments, segments[1:]))
    assert segments[0].tokens[0] == 1000 and segments[-1].tokens[-1] == 1001


def test_raw_native_data_contains_no_reward_or_chat_instructions():
    docs = tuple(FileRef(f"source/{name}.txt", BlobRef.of(name.encode())) for name in ("first", "second"))
    segments = tuple(segment for doc in docs for segment in segment_document(doc, (1, 2, 3, 4), encoding()))
    corpus = RawCorpus(BlobRef.of(b"frozen"), encoding(), segments)
    assert RawCorpus.from_bytes(corpus.to_bytes()) == corpus
    data = native_train_data(segments, first_index=13)
    assert "rewards" not in data and "raw_reward" not in data and "rollout_log_probs" not in data
    assert data["sample_indices"] == data["rollout_ids"] == [13, 14, 15, 16]
    assert data["response_lengths"] == [3, 2, 3, 2]
    assert data["loss_masks"] == [[1, 1, 1], [1, 1], [1, 1, 1], [1, 1]]
    assert data["tokens"][2][0] == encoding().bos_id  # Fresh context for the second document.
    with pytest.raises(ValueError, match="actual source segments"):
        native_train_data(())


@pytest.mark.parametrize("changes", [{"bos_id": True}, {"eos_id": -1}, {"context_tokens": 1}, {"context_tokens": False}])
def test_encoding_requires_explicit_meaningful_bounds(changes):
    with pytest.raises(ValueError):
        replace(encoding(), **changes)


def test_segment_rejects_boolean_and_context_only_samples():
    document = FileRef("source/file", BlobRef.of(b"source"))
    for tokens in ((1,), (1, True), (-1, 2)):
        with pytest.raises(ValueError):
            RawSegment(document, 0, tokens)


def test_real_tokenizer_no_inherited_truncation_padding_or_chat_template(tmp_path, monkeypatch):
    tokenizers = pytest.importorskip("tokenizers", reason="run tokenizer checks in the native dependency lane")
    from importlib.metadata import version

    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel(
        {"UNK": 0, "original": 1, "source": 2, "text": 3, "BOS": 4, "EOS": 5, "PAD": 6}, unk_token="UNK"))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(1)
    tokenizer.enable_padding(length=32, pad_id=6, pad_token="PAD")
    store = DirectoryStore(tmp_path / "objects")
    policy = RawEncoding(store.put(tokenizer.to_str().encode()), "tokenizers/" + version("tokenizers"), 4, 5, 3)
    reference = store.put(b"original source text")
    files = (FileData(FileRef("source/kept.txt", reference), store.read(reference)),)
    # Material authorization is covered by the real supervised acquisition and
    # export lane. This test isolates the installed native tokenizer adapter.
    monkeypatch.setattr("behave.train.training_data.raw_documents", lambda reader, ref: files)
    corpus = tokenize_materials(store, BlobRef.of(b"materials"), policy)
    assert [sample.tokens for sample in corpus.segments] == [(4, 1, 2), (2, 3, 5)]
    saved = store.put(corpus.to_bytes())
    assert load_raw_corpus(store, saved) == corpus
    changed = replace(corpus, segments=(replace(corpus.segments[0], tokens=(4, 0, 0)), *corpus.segments[1:]))
    with pytest.raises(RecordError, match="frozen external documents"):
        load_raw_corpus(store, store.put(changed.to_bytes()))
    with pytest.raises(RecordError, match="implementation differs"):
        tokenize_materials(store, corpus.materials, replace(policy, implementation="unbound-tokenizer"))
    with pytest.raises(RecordError, match="entries in the pinned tokenizer"):
        tokenize_materials(store, corpus.materials, replace(policy, bos_id=100))
