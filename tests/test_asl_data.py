import random
import unittest

from ml.asl.data import CLASSES, HashIndex, split_records


class ASLDataTests(unittest.TestCase):
    def test_radius_two_index_matches_brute_force(self):
        rng = random.Random(9)
        values = [rng.getrandbits(64) for _ in range(200)]
        index = HashIndex()
        for value in values:
            index.add(value)
        queries = [rng.getrandbits(64) for _ in range(100)]
        for value in values[:10]:
            queries.extend([value, value ^ 1, value ^ (1 << 23) ^ (1 << 49), value ^ 7])
        for query in queries:
            self.assertEqual(index.contains_near(query), any((v ^ query).bit_count() <= 2 for v in values))

    def fixture(self):
        rng = random.Random(123)
        return [dict(label=label, path=f"{label}/{i}.jpg", number=i,
                     pixels_sha256=f"{label}/{i}", dhash=rng.getrandbits(64))
                for label in CLASSES for i in range(1, 201)]

    def test_reproducible_disjoint_grouped_splits(self):
        records = self.fixture()
        retained, rejected = split_records(records, 42, 20)
        self.assertEqual((retained, rejected), split_records(list(reversed(records)), 42, 20))
        self.assertFalse(rejected)
        groups = {}
        for record in retained:
            key = record["group"]
            self.assertEqual(groups.setdefault(key, record["split"]), record["split"])
        self.assertEqual(len(retained), len(records))

    def test_near_duplicates_quarantined_instead_of_leaked(self):
        records = self.fixture()
        retained, _ = split_records(records, 42, 20)
        train = next(r for r in retained if r["split"] == "train")
        test = next(r for r in retained if r["split"] == "test")
        for record in records:
            if record["path"] == test["path"]:
                record["dhash"] = train["dhash"] ^ 1
        retained, rejected = split_records(records, 42, 20)
        self.assertNotIn(test["path"], [r["path"] for r in retained])
        self.assertEqual([r["path"] for r in rejected], [test["path"]])


if __name__ == "__main__":
    unittest.main()
