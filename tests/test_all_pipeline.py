# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import sys
import shutil
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import qlib
from qlib.config import REG_CN, C
from qlib.utils import drop_nan_by_y_index
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.data.handler import Alpha158
from qlib.contrib.strategy.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import (
    backtest as normal_backtest,
    risk_analysis,
)
from qlib.utils import exists_qlib_data, init_instance_by_config, flatten_dict
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, SigAnaRecord, PortAnaRecord


market = "csi300"
benchmark = "SH000300"

###################################
# train model
###################################
data_handler_config = {
    "start_time": "2008-01-01",
    "end_time": "2020-08-01",
    "fit_start_time": "2008-01-01",
    "fit_end_time": "2014-12-31",
    "instruments": market,
}

task = {
    "model": {
        "class": "LGBModel",
        "module_path": "qlib.contrib.model.gbdt",
        "kwargs": {
            "loss": "mse",
            "colsample_bytree": 0.8879,
            "learning_rate": 0.0421,
            "subsample": 0.8789,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 210,
            "num_threads": 20,
        },
    },
    "dataset": {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": data_handler_config,
            },
            "segments": {
                "train": ("2008-01-01", "2014-12-31"),
                "valid": ("2015-01-01", "2016-12-31"),
                "test": ("2017-01-01", "2020-08-01"),
            },
        },
    },
}

port_analysis_config = {
    "strategy": {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy.strategy",
        "kwargs": {
            "topk": 50,
            "n_drop": 5,
        },
    },
    "backtest": {
        "verbose": False,
        "limit_threshold": 0.095,
        "account": 100000000,
        "benchmark": benchmark,
        "deal_price": "close",
        "open_cost": 0.0005,
        "close_cost": 0.0015,
        "min_cost": 5,
    },
}


# train
def train():
    """train model

    Returns
    -------
        pred_score: pandas.DataFrame
            predict scores
        performance: dict
            model performance
    """

    # model initiaiton
    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])

    # start exp
    with R.start(experiment_name="workflow"):
        R.log_params(**flatten_dict(task))
        model.fit(dataset)

        # prediction
        recorder = R.get_recorder()
        rid = recorder.id
        sr = SignalRecord(model, dataset, recorder)
        sr.generate()
        pred_score = sr.load()

        # calculate ic and ric
        sar = SigAnaRecord(recorder)
        sar.generate()
        ic = sar.load(sar.get_path("ic.pkl"))
        ric = sar.load(sar.get_path("ric.pkl"))

    return pred_score, {"ic": ic, "ric": ric}, rid


def backtest_analysis(pred, rid):
    """backtest and analysis

    Parameters
    ----------
    pred : pandas.DataFrame
        predict scores
    rid : str
        the id of the recorder to be used in this function

    Returns
    -------
    analysis : pandas.DataFrame
        the analysis result

    """
    recorder = R.get_recorder(experiment_name="workflow", recorder_id=rid)
    # backtest
    par = PortAnaRecord(recorder, port_analysis_config)
    par.generate()
    analysis_df = par.load(par.get_path("port_analysis.pkl"))
    print(analysis_df)
    return analysis_df


class TestAllFlow(unittest.TestCase):
    PRED_SCORE = None
    REPORT_NORMAL = None
    POSITIONS = None
    RID = None

    @classmethod
    def setUpClass(cls) -> None:
        # use default data
        provider_uri = "~/.qlib/qlib_data/cn_data_simple"  # target_dir
        if not exists_qlib_data(provider_uri):
            print(f"Qlib data is not found in {provider_uri}")
            sys.path.append(str(Path(__file__).resolve().parent.parent.joinpath("scripts")))
            from get_data import GetData

            GetData().qlib_data(
                name="qlib_data_simple", region="cn", version="latest", interval="1d", target_dir=provider_uri
            )
        qlib.init(provider_uri=provider_uri, region=REG_CN)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(str(Path(C["exp_manager"]["kwargs"]["uri"].strip("file:")).resolve()))

    def test_0_train(self):
        TestAllFlow.PRED_SCORE, ic_ric, TestAllFlow.RID = train()
        self.assertGreaterEqual(ic_ric["ic"].all(), 0, "train failed")
        self.assertGreaterEqual(ic_ric["ric"].all(), 0, "train failed")

    def test_1_backtest(self):
        analyze_df = backtest_analysis(TestAllFlow.PRED_SCORE, TestAllFlow.RID)
        self.assertGreaterEqual(
            analyze_df.loc(axis=0)["excess_return_with_cost", "annualized_return"].values[0],
            0.10,
            "backtest failed",
        )


def suite():
    _suite = unittest.TestSuite()
    _suite.addTest(TestAllFlow("test_0_train"))
    _suite.addTest(TestAllFlow("test_1_backtest"))
    return _suite


if __name__ == "__main__":
    runner = unittest.TextTestRunner()
    runner.run(suite())
