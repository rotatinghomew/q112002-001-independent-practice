import unittest

from record_reader import read_records


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_records()

    def references(self, left, left_key, right, right_key):
        candidates = {row[right_key] for row in self.tables[right]}
        self.assertTrue(all(row[left_key] in candidates for row in self.tables[left]))

    def test_documents_reference_source_records(self):
        self.references('attempts','student','students','student')
        self.references('marks','goal','objectives','goal')
        self.references('withdrawals','mark','marks','mark')
        self.references('receipts','publication','minutes','publication')
        self.references('assignments','anchor','anchors','anchor')
        attempts = self.tables['attempts']
        hint = self.tables['hints'][0]
        before = next(row for row in attempts if row['attempt']=='a1')
        after = next(row for row in attempts if row['attempt']=='a2')
        self.assertEqual(before['activity'], after['activity'])
        self.assertLess(before['minute'], hint['minute'])
        self.assertLess(hint['minute'], after['minute'])
