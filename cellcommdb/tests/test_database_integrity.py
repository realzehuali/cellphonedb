import pandas as pd
from flask_testing import TestCase

from cellcommdb.api import create_app, current_dir
from cellcommdb.config import TestConfig
from cellcommdb.extensions import db
from cellcommdb.models import Gene


class DatabaseIntegrity(TestCase):
    def test_gene(self):
        query = db.session.query(Gene)
        dataframe = pd.read_sql(query.statement, db.engine)

        dataframe.drop('id', inplace=True, axis=1)
        duplicated_genes = dataframe[dataframe.duplicated()]
        if len(duplicated_genes):
            duplicated_genes.to_csv('%s/../out/duplicated_genes.csv' % current_dir, index=False)

        self.assertEqual(len(duplicated_genes), 0,
                         'There are %s duplicated genes in database. Please check duplicated_genes.csv file' % len(
                             duplicated_genes))

    def create_app(self):
        return create_app(TestConfig)

    def setUp(self):
        self.client = self.app.test_client()