# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.


# This script reuses some code from
# https://github.com/huggingface/pytorch-pretrained-BERT/blob/master/examples
# /run_classifier.py


from enum import Enum
import warnings
from collections import Iterable, namedtuple
import torch
from tqdm import tqdm

from pytorch_transformers.tokenization_bert import BertTokenizer, whitespace_tokenize

from torch.utils.data import (
    DataLoader,
    RandomSampler,
    SequentialSampler,
    TensorDataset,
)

from utils_nlp.models.bert.qa_utils import QAFeatures, QAExample

# Max supported sequence length
BERT_MAX_LEN = 512


class Language(Enum):
    """An enumeration of the supported pretrained models and languages."""

    ENGLISH = "bert-base-uncased"
    ENGLISHCASED = "bert-base-cased"
    ENGLISHLARGE = "bert-large-uncased"
    ENGLISHLARGECASED = "bert-large-cased"
    ENGLISHLARGEWWM = "bert-large-uncased-whole-word-masking"
    ENGLISHLARGECASEDWWM = "bert-large-cased-whole-word-masking"
    CHINESE = "bert-base-chinese"
    MULTILINGUAL = "bert-base-multilingual-cased"


class Tokenizer:
    def __init__(
        self, language=Language.ENGLISH, to_lower=False, cache_dir="."
    ):
        """Initializes the underlying pretrained BERT tokenizer.

        Args:
            language (Language, optional): The pretrained model's language.
                                           Defaults to Language.ENGLISH.
            cache_dir (str, optional): Location of BERT's cache directory.
                Defaults to ".".
        """
        self.tokenizer = BertTokenizer.from_pretrained(
            language.value, do_lower_case=to_lower, cache_dir=cache_dir
        )
        self.language = language

    def tokenize(self, text):
        """Tokenizes a list of documents using a BERT tokenizer

        Args:
            text (list): List of strings (one sequence) or
                tuples (two sequences).

        Returns:
            [list]: List of lists. Each sublist contains WordPiece tokens
                of the input sequence(s).
        """
        if isinstance(text[0], str):
            return [self.tokenizer.tokenize(x) for x in tqdm(text)]
        else:
            return [
                [self.tokenizer.tokenize(x) for x in sentences]
                for sentences in tqdm(text)
            ]

    def _truncate_seq_pair(self, tokens_a, tokens_b, max_length):
        """Truncates a sequence pair in place to the maximum length."""
        # This is a simple heuristic which will always truncate the longer
        # sequence one token at a time. This makes more sense than
        # truncating an equal percent of tokens from each, since if one
        # sequence is very short then each token that's truncated likely
        # contains more information than a longer sequence.
        while True:
            total_length = len(tokens_a) + len(tokens_b)
            if total_length <= max_length:
                break
            if len(tokens_a) > len(tokens_b):
                tokens_a.pop()
            else:
                tokens_b.pop()

        tokens_a.append("[SEP]")
        tokens_b.append("[SEP]")

        return [tokens_a, tokens_b]

    def preprocess_classification_tokens(self, tokens, max_len=BERT_MAX_LEN):
        """Preprocessing of input tokens:
            - add BERT sentence markers ([CLS] and [SEP])
            - map tokens to token indices in the BERT vocabulary
            - pad and truncate sequences
            - create an input_mask
            - create token type ids, aka. segment ids

        Args:
            tokens (list): List of token lists to preprocess.
            max_len (int, optional): Maximum number of tokens
                            (documents will be truncated or padded).
                            Defaults to 512.
        Returns:
            tuple: A tuple containing the following three lists
                list of preprocesssed token lists
                list of input mask lists
                list of token type id lists
        """
        if max_len > BERT_MAX_LEN:
            print(
                "setting max_len to max allowed tokens: {}".format(
                    BERT_MAX_LEN
                )
            )
            max_len = BERT_MAX_LEN

        if isinstance(tokens[0][0], str):
            tokens = [x[0 : max_len - 2] + ["[SEP]"] for x in tokens]
            token_type_ids = None
        else:
            # get tokens for each sentence [[t00, t01, ...] [t10, t11,... ]]
            tokens = [
                self._truncate_seq_pair(sentence[0], sentence[1], max_len - 3)
                for sentence in tokens
            ]

            # construct token_type_ids
            # [[0, 0, 0, 0, ... 0, 1, 1, 1, ... 1], [0, 0, 0, ..., 1, 1, ]
            token_type_ids = [
                [[i] * len(sentence) for i, sentence in enumerate(example)]
                for example in tokens
            ]
            # merge sentences
            tokens = [
                [token for sentence in example for token in sentence]
                for example in tokens
            ]
            # prefix with [0] for [CLS]
            token_type_ids = [
                [0] + [i for sentence in example for i in sentence]
                for example in token_type_ids
            ]
            # pad sequence
            token_type_ids = [
                x + [0] * (max_len - len(x)) for x in token_type_ids
            ]

        tokens = [["[CLS]"] + x for x in tokens]
        # convert tokens to indices
        tokens = [self.tokenizer.convert_tokens_to_ids(x) for x in tokens]
        # pad sequence
        tokens = [x + [0] * (max_len - len(x)) for x in tokens]
        # create input mask
        input_mask = [[min(1, x) for x in y] for y in tokens]
        return tokens, input_mask, token_type_ids

    def tokenize_ner(
        self,
        text,
        max_len=BERT_MAX_LEN,
        labels=None,
        label_map=None,
        trailing_piece_tag="X",
    ):
        """
        Tokenize and preprocesses input word lists, involving the following steps
            0. WordPiece tokenization.
            1. Convert string tokens to token ids.
            2. Convert input labels to label ids, if labels and label_map are
                provided.
            3. If a word is tokenized into multiple pieces of tokens by the
                WordPiece tokenizer, label the extra tokens with
                trailing_piece_tag.
            4. Pad or truncate input text according to max_seq_length
            5. Create input_mask for masking out padded tokens.

        Args:
            text (list): List of lists. Each sublist is a list of words in an
                input sentence.
            max_len (int, optional): Maximum length of the list of
                tokens. Lists longer than this are truncated and shorter
                ones are padded with "O"s. Default value is BERT_MAX_LEN=512.
            labels (list, optional): List of word label lists. Each sublist
                contains labels corresponding to the input word list. The lengths
                of the label list and word list must be the same. Default
                value is None.
            label_map (dict, optional): Dictionary for mapping original token
                labels (which may be string type) to integers. Default value
                is None.
            trailing_piece_tag (str, optional): Tag used to label trailing
                word pieces. For example, "criticize" is broken into "critic"
                and "##ize", "critic" preserves its original label and "##ize"
                is labeled as trailing_piece_tag. Default value is "X".

        Returns:
            tuple: A tuple containing the following four lists.
                1. input_ids_all: List of lists. Each sublist contains
                    numerical values, i.e. token ids, corresponding to the
                    tokens in the input text data.
                2. input_mask_all: List of lists. Each sublist
                    contains the attention mask of the input token id list,
                    1 for input tokens and 0 for padded tokens, so that
                    padded tokens are not attended to.
                3. trailing_token_mask: List of lists. Each sublist is
                    a boolean list, True for the first word piece of each
                    original word, False for the trailing word pieces,
                    e.g. "##ize". This mask is useful for removing the
                    predictions on trailing word pieces, so that each
                    original word in the input text has a unique predicted
                    label.
                4. label_ids_all: List of lists of numerical labels,
                    each sublist contains token labels of a input
                    sentence/paragraph, if labels is provided. If the `labels`
                    argument is not provided, the value of this is None.
        """

        def _is_iterable_but_not_string(obj):
            return isinstance(obj, Iterable) and not isinstance(obj, str)

        if max_len > BERT_MAX_LEN:
            warnings.warn(
                "setting max_len to max allowed tokens: {}".format(
                    BERT_MAX_LEN
                )
            )
            max_len = BERT_MAX_LEN

        if not _is_iterable_but_not_string(text):
            # The input text must be an non-string Iterable
            raise ValueError(
                "Input text must be an iterable and not a string."
            )
        else:
            # If the input text is a single list of words, convert it to
            # list of lists for later iteration
            if not _is_iterable_but_not_string(text[0]):
                text = [text]
        if labels is not None:
            if not _is_iterable_but_not_string(labels):
                raise ValueError(
                    "labels must be an iterable and not a string."
                )
            else:
                if not _is_iterable_but_not_string(labels[0]):
                    labels = [labels]

        label_available = True
        if labels is None:
            label_available = False
            # create an artificial label list for creating trailing token mask
            labels = [["O"] * len(t) for t in text]

        input_ids_all = []
        input_mask_all = []
        label_ids_all = []
        trailing_token_mask_all = []
        for t, t_labels in zip(text, labels):

            if len(t) != len(t_labels):
                raise ValueError(
                    "The number of words is {0}, but the number of labels is {1}.".format(
                        len(t), len(t_labels)
                    )
                )

            new_labels = []
            new_tokens = []
            if label_available:
                for word, tag in zip(t, t_labels):
                    sub_words = self.tokenizer.tokenize(word)
                    for count, sub_word in enumerate(sub_words):
                        if count > 0:
                            tag = trailing_piece_tag
                        new_labels.append(tag)
                        new_tokens.append(sub_word)
            else:
                for word in t:
                    sub_words = self.tokenizer.tokenize(word)
                    for count, sub_word in enumerate(sub_words):
                        if count > 0:
                            tag = trailing_piece_tag
                        else:
                            tag = "O"
                        new_labels.append(tag)
                        new_tokens.append(sub_word)

            if len(new_tokens) > max_len:
                new_tokens = new_tokens[:max_len]
                new_labels = new_labels[:max_len]
            input_ids = self.tokenizer.convert_tokens_to_ids(new_tokens)

            # The mask has 1 for real tokens and 0 for padding tokens.
            # Only real tokens are attended to.
            input_mask = [1.0] * len(input_ids)

            # Zero-pad up to the max sequence length.
            padding = [0.0] * (max_len - len(input_ids))
            label_padding = ["O"] * (max_len - len(input_ids))

            input_ids += padding
            input_mask += padding
            new_labels += label_padding

            trailing_token_mask_all.append(
                [
                    True if label != trailing_piece_tag else False
                    for label in new_labels
                ]
            )

            if label_map:
                label_ids = [label_map[label] for label in new_labels]
            else:
                label_ids = new_labels

            input_ids_all.append(input_ids)
            input_mask_all.append(input_mask)
            label_ids_all.append(label_ids)

        if label_available:
            return (
                input_ids_all,
                input_mask_all,
                trailing_token_mask_all,
                label_ids_all,
            )
        else:
            return input_ids_all, input_mask_all, trailing_token_mask_all, None

    def tokenize_qa(
        self,
        doc_text,
        question_text,
        answer_start,
        answer_text,
        is_training,
        max_query_length=64,
        max_len=BERT_MAX_LEN,
        doc_stride=128,
        qa_id=None,
        is_impossible=None):

        _DocSpan = namedtuple("DocSpan", ["start", "length"])

        def _is_whitespace(c):
            if c == " " or c == "\t" or c == "\r" or c == "\n" or ord(c) == 0x202F:
                return True
            return False

        def _is_iterable_but_not_string(obj):
            return isinstance(obj, Iterable) and not isinstance(obj, str)

        def _improve_answer_span(doc_tokens, input_start, input_end, tokenizer,
                                orig_answer_text):
            """Returns tokenized answer spans that better match the annotated answer."""

            # We first project character-based annotations to
            # whitespace-tokenized words. But then after WordPiece tokenization, we can
            # often find a "better match". For example:
            #
            #   Question: What year was John Smith born?
            #   Context: The leader was John Smith (1895-1943).
            #   Answer: 1895
            #
            # The original whitespace-tokenized answer will be "(1895-1943).". However
            # after tokenization, our tokens will be "( 1895 - 1943 ) .". So we can match
            # the exact answer, 1895.
            #
            # However, this is not always possible. Consider the following:
            #
            #   Question: What country is the top exporter of electornics?
            #   Context: The Japanese electronics industry is the lagest in the world.
            #   Answer: Japan
            #
            # In this case, the annotator chose "Japan" as a character sub-span of
            # the word "Japanese". Since our WordPiece tokenizer does not split
            # "Japanese", we just use "Japanese" as the annotation. This is fairly rare,
            # but does happen.
            tok_answer_text = " ".join(tokenizer.tokenize(orig_answer_text))

            for new_start in range(input_start, input_end + 1):
                for new_end in range(input_end, new_start - 1, -1):
                    text_span = " ".join(doc_tokens[new_start:(new_end + 1)])
                    if text_span == tok_answer_text:
                        return (new_start, new_end)

            return (input_start, input_end)

        def _check_is_max_context(doc_spans, cur_span_index, position):
            """Check if this is the 'max context' doc span for the token."""

            # Because of the sliding window approach taken to scoring documents, a single
            # token can appear in multiple documents. E.g.
            #  Doc: the man went to the store and bought a gallon of milk
            #  Span A: the man went to the
            #  Span B: to the store and bought
            #  Span C: and bought a gallon of
            #  ...
            #
            # Now the word 'bought' will have two scores from spans B and C. We only
            # want to consider the score with "maximum context", which we define as
            # the *minimum* of its left and right context (the *sum* of left and
            # right context will always be the same, of course).
            #
            # In the example the maximum context for 'bought' would be span C since
            # it has 1 left context and 3 right context, while span B has 4 left context
            # and 0 right context.
            best_score = None
            best_span_index = None
            for (span_index, doc_span) in enumerate(doc_spans):
                end = doc_span.start + doc_span.length - 1
                if position < doc_span.start:
                    continue
                if position > end:
                    continue
                num_left_context = position - doc_span.start
                num_right_context = end - position
                score = min(num_left_context, num_right_context) + 0.01 * doc_span.length
                if best_score is None or score > best_score:
                    best_score = score
                    best_span_index = span_index

            return cur_span_index == best_span_index


        if qa_id is None:
            qa_id = list(range(len(question_text)))

        if is_impossible is None:
            is_impossible = [False] * len(question_text)

        qa_examples = []
        for d_text, q_text, a_start, a_text, q_id, impossible in \
            zip(doc_text, question_text, answer_start, answer_text, qa_id, is_impossible):
            d_tokens = []
            char_to_word_offset = []
            prev_is_whitespace = True
            for c in d_text:
                if _is_whitespace(c):
                    prev_is_whitespace = True
                else:
                    if prev_is_whitespace:
                        d_tokens.append(c)
                    else:
                        d_tokens[-1] += c
                    prev_is_whitespace = False
                char_to_word_offset.append(len(d_tokens) - 1)

            if _is_iterable_but_not_string(a_start):
                if len(a_start) != len(a_text):
                    raise Exception("The lengths of answer starts and answer texts are different.")
                if len(a_start) > 1 and is_training and not impossible:
                    raise Exception("For training, each question should have exactly 1 answer.")
            else:
                a_start = [a_start]
                a_text = [a_text]

            for s, t in zip(a_start, a_text):
                start_position = None
                end_position = None
                if is_training:
                    if not impossible:
                        answer_length = len(t)
                        start_position = char_to_word_offset[s]
                        end_position = char_to_word_offset[s + answer_length - 1]
                        # Only add answers where the text can be exactly recovered from the
                        # document. If this CAN'T happen it's likely due to weird Unicode
                        # stuff so we will just skip the example.
                        #
                        # Note that this means for training mode, every example is NOT
                        # guaranteed to be preserved.
                        actual_text = " ".join(d_tokens[start_position:(end_position + 1)])
                        cleaned_answer_text = " ".join(
                            whitespace_tokenize(t))
                        if actual_text.find(cleaned_answer_text) == -1:
                            logger.warning("Could not find answer: '%s' vs. '%s'",
                                        actual_text, cleaned_answer_text)
                            continue
                    else:
                        start_position = -1
                        end_position = -1

                qa_examples.append(
                    QAExample(qa_id=q_id,
                                doc_tokens=d_tokens,
                                question_text = q_text,
                                orig_answer_text=t,
                                start_position=start_position,
                                end_position=end_position,
                                is_impossible=impossible))

        cls_token = '[CLS]'
        sep_token = '[SEP]'
        pad_token = 0
        sequence_a_segment_id = 0
        sequence_b_segment_id = 1
        cls_token_segment_id = 0
        pad_token_segment_id = 0
        cls_token_at_end = False
        mask_padding_with_zero=True

        unique_id = 1000000000
        features = []
        for (example_index, example) in enumerate(qa_examples):
            query_tokens = self.tokenizer.tokenize(example.question_text)

            if len(query_tokens) > max_query_length:
                query_tokens = query_tokens[0:max_query_length]

            tok_to_orig_index = []
            orig_to_tok_index = []
            all_doc_tokens = []
            for (i, token) in enumerate(example.doc_tokens):
                orig_to_tok_index.append(len(all_doc_tokens))
                sub_tokens = self.tokenizer.tokenize(token)
                for sub_token in sub_tokens:
                    tok_to_orig_index.append(i)
                    all_doc_tokens.append(sub_token)

            tok_start_position = None
            tok_end_position = None
            if is_training and example.is_impossible:
                tok_start_position = -1
                tok_end_position = -1
            if is_training and not example.is_impossible:
                tok_start_position = orig_to_tok_index[example.start_position]
                if example.end_position < len(example.doc_tokens) - 1:
                    tok_end_position = orig_to_tok_index[example.end_position + 1] - 1
                else:
                    tok_end_position = len(all_doc_tokens) - 1
                (tok_start_position, tok_end_position) = _improve_answer_span(
                    all_doc_tokens, tok_start_position, tok_end_position, self.tokenizer,
                    example.orig_answer_text)

            # The -3 accounts for [CLS], [SEP] and [SEP]
            max_tokens_for_doc = max_len - len(query_tokens) - 3

            # We can have documents that are longer than the maximum sequence length.
            # To deal with this we do a sliding window approach, where we take chunks
            # of the up to our max length with a stride of `doc_stride`.

            doc_spans = []
            start_offset = 0
            while start_offset < len(all_doc_tokens):
                length = len(all_doc_tokens) - start_offset
                if length > max_tokens_for_doc:
                    length = max_tokens_for_doc
                doc_spans.append(_DocSpan(start=start_offset, length=length))
                if start_offset + length == len(all_doc_tokens):
                    break
                start_offset += min(length, doc_stride)

            for (doc_span_index, doc_span) in enumerate(doc_spans):
                tokens = []
                token_to_orig_map = {}
                token_is_max_context = {}
                segment_ids = []

                # p_mask: mask with 1 for token than cannot be in the answer (0 for token which can be in an answer)
                # Original TF implem also keep the classification token (set to 0) (not sure why...)
                p_mask = []

                # CLS token at the beginning
                if not cls_token_at_end:
                    tokens.append(cls_token)
                    segment_ids.append(cls_token_segment_id)
                    p_mask.append(0)
                    cls_index = 0

                # Query
                for token in query_tokens:
                    tokens.append(token)
                    segment_ids.append(sequence_a_segment_id)
                    p_mask.append(1)

                # SEP token
                tokens.append(sep_token)
                segment_ids.append(sequence_a_segment_id)
                p_mask.append(1)

                # Paragraph
                for i in range(doc_span.length):
                    split_token_index = doc_span.start + i
                    token_to_orig_map[len(tokens)] = tok_to_orig_index[split_token_index]

                    is_max_context = _check_is_max_context(doc_spans, doc_span_index,
                                                        split_token_index)
                    token_is_max_context[len(tokens)] = is_max_context
                    tokens.append(all_doc_tokens[split_token_index])
                    segment_ids.append(sequence_b_segment_id)
                    p_mask.append(0)
                paragraph_len = doc_span.length

                # SEP token
                tokens.append(sep_token)
                segment_ids.append(sequence_b_segment_id)
                p_mask.append(1)

                # CLS token at the end
                if cls_token_at_end:
                    tokens.append(cls_token)
                    segment_ids.append(cls_token_segment_id)
                    p_mask.append(0)
                    cls_index = len(tokens) - 1  # Index of classification token

                input_ids = self.tokenizer.convert_tokens_to_ids(tokens)

                # The mask has 1 for real tokens and 0 for padding tokens. Only real
                # tokens are attended to.
                input_mask = [1 if mask_padding_with_zero else 0] * len(input_ids)

                # Zero-pad up to the sequence length.
                while len(input_ids) < max_len:
                    input_ids.append(pad_token)
                    input_mask.append(0 if mask_padding_with_zero else 1)
                    segment_ids.append(pad_token_segment_id)
                    p_mask.append(1)

                assert len(input_ids) == max_len
                assert len(input_mask) == max_len
                assert len(segment_ids) == max_len

                span_is_impossible = example.is_impossible
                start_position = None
                end_position = None
                if is_training and not span_is_impossible:
                    # For training, if our document chunk does not contain an annotation
                    # we throw it out, since there is nothing to predict.
                    doc_start = doc_span.start
                    doc_end = doc_span.start + doc_span.length - 1
                    out_of_span = False
                    if not (tok_start_position >= doc_start and
                            tok_end_position <= doc_end):
                        out_of_span = True
                    if out_of_span:
                        start_position = 0
                        end_position = 0
                        span_is_impossible = True
                    else:
                        doc_offset = len(query_tokens) + 2
                        start_position = tok_start_position - doc_start + doc_offset
                        end_position = tok_end_position - doc_start + doc_offset

                if is_training and span_is_impossible:
                    start_position = cls_index
                    end_position = cls_index

                features.append(
                    QAFeatures(
                        unique_id=unique_id,
                        example_index=example_index,
                        tokens=tokens,
                        token_to_orig_map=token_to_orig_map,
                        token_is_max_context=token_is_max_context,
                        input_ids=input_ids,
                        input_mask=input_mask,
                        segment_ids=segment_ids,
                        paragraph_len=paragraph_len,
                        start_position=start_position,
                        end_position=end_position))
                unique_id += 1

        return features, qa_examples


def create_data_loader(
    input_ids,
    input_mask,
    label_ids=None,
    sample_method="random",
    batch_size=32,
):
    """
    Create a dataloader for sampling and serving data batches.

    Args:
        input_ids (list): List of lists. Each sublist contains numerical
            values, i.e. token ids, corresponding to the tokens in the input
            text data.
        input_mask (list): List of lists. Each sublist contains the attention
            mask of the input token id list, 1 for input tokens and 0 for
            padded tokens, so that padded tokens are not attended to.
        label_ids (list, optional): List of lists of numerical labels,
            each sublist contains token labels of a input
            sentence/paragraph. Default value is None.
        sample_method (str, optional): Order of data sampling. Accepted
            values are "random", "sequential". Default value is "random".
        batch_size (int, optional): Number of samples used in each training
            iteration. Default value is 32.

    Returns:
        DataLoader: A Pytorch Dataloader containing the input_ids tensor,
            input_mask tensor, and label_ids (if provided) tensor.

    """
    input_ids_tensor = torch.tensor(input_ids, dtype=torch.long)
    input_mask_tensor = torch.tensor(input_mask, dtype=torch.long)

    if label_ids:
        label_ids_tensor = torch.tensor(label_ids, dtype=torch.long)
        tensor_data = TensorDataset(
            input_ids_tensor, input_mask_tensor, label_ids_tensor
        )
    else:
        tensor_data = TensorDataset(input_ids_tensor, input_mask_tensor)

    if sample_method == "random":
        sampler = RandomSampler(tensor_data)
    elif sample_method == "sequential":
        sampler = SequentialSampler(tensor_data)
    else:
        raise ValueError(
            "Invalid sample_method value, accepted values are: "
            "random, sequential, and distributed"
        )

    dataloader = DataLoader(
        tensor_data, sampler=sampler, batch_size=batch_size
    )

    return dataloader