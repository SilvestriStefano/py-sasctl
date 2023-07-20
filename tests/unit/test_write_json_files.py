#!/usr/bin/env python
# encoding: utf-8
#
# Copyright © 2023, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import io
import json
import os
import pickle
import random
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import sasctl.pzmm as pzmm
from sasctl.pzmm.write_json_files import JSONFiles as jf

# Example input variable list from hmeq dataset (generated by mlflow_model.py)
input_dict = [
    {"name": "LOAN", "type": "long"},
    {"name": "MORTDUE", "type": "double"},
    {"name": "VALUE", "type": "double"},
    {"name": "YOJ", "type": "double"},
    {"name": "DEROG", "type": "double"},
    {"name": "DELINQ", "type": "double"},
    {"name": "CLAGE", "type": "double"},
    {"name": "NINQ", "type": "double"},
    {"name": "CLNO", "type": "double"},
    {"name": "DEBTINC", "type": "double"},
    {"name": "JOB", "type": "string"},
    {"name": "REASON_HomeImp", "type": "integer"},
]


@pytest.fixture
def change_dir():
    """Change working directory for the duration of the test."""
    old_dir = os.path.abspath(os.curdir)

    def change(new_dir):
        os.chdir(new_dir)

    yield change
    os.chdir(old_dir)


def test_flatten_list():
    """
    Test cases:
    - Single list of strings
    - Nested list of strings
    - Nested list of ints
    """
    str_list = ["a", "bad", "good", "Z"]
    assert list(pzmm.write_json_files._flatten(str_list)) == str_list

    nested_str = ["a", ["bad", "good", "Z"]]
    assert list(pzmm.write_json_files._flatten(nested_str)) == str_list

    nested_int = [1, 2, [3, 4, 5], 6, 7, [8, 9]]
    int_list = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert list(pzmm.write_json_files._flatten(nested_int)) == int_list


def test_check_if_string():
    """
    Test cases:
    - If/else statements
    """
    data = [
        {"type": "string"},
        {"type": "double"},
        {"type": "tensor", "tensor-spec": {"dtype": "string"}},
        {"type": "tensor", "tensor-spec": {"dtype": "double"}},
    ]
    test = [True, False, True, False]
    for d, t in zip(data, test):
        assert jf.check_if_string(d) is t


def test_generate_variable_properties(hmeq_dataset):
    """
    Test cases:
    - Return expected variable properties from hmeq dataset
    - Include case of passing a series
    """
    df = hmeq_dataset
    # Generate the variable properties from the dataset (excluding the target variable)
    dict_list = jf.generate_variable_properties(df.drop(["BAD"], axis=1))
    # Find all expected variables
    assert len(dict_list) == 12
    # Verify expected variable properties
    for var in dict_list:
        if var["name"] in ["REASON", "JOB"]:
            assert var["level"] == "nominal" and var["type"] == "string"
        else:
            assert var["level"] == "interval" and var["type"] == "decimal"

    df = pd.Series([1, 2, 3, 4, 5], dtype="category", name="cat")
    dict_list = jf.generate_variable_properties(df)
    assert len(dict_list) == 1
    assert dict_list[0]["level"] == "nominal" and dict_list[0]["type"] == "decimal"


def test_generate_mlflow_variable_properties():
    """
    Test cases:
    - Return expected number of variables from mlflow_model.py output
    """
    dict_list = jf.generate_mlflow_variable_properties(input_dict)
    assert len(dict_list) == 12

    tensor_dict = [
        {"type": "string"},
        {"type": "double"},
        {"type": "tensor", "tensor-spec": {"dtype": "string"}},
        {"type": "tensor", "tensor-spec": {"dtype": "double"}},
    ]
    dict_list = jf.generate_mlflow_variable_properties(tensor_dict)
    assert len(dict_list) == 4


def test_write_var_json(hmeq_dataset):
    """
    Test cases:
    - Generate correctly named file based on is_input (assuming path provided)
    - Return correctly labelled dict based on is_input (assuming no path provided)
    - Return correctly labelled dict from Mlflow model dataset
    """
    df = hmeq_dataset
    with tempfile.TemporaryDirectory() as tmp_dir:
        jf.write_var_json(df, True, Path(tmp_dir))
        assert (Path(tmp_dir) / "inputVar.json").exists()
        jf.write_var_json(df, False, Path(tmp_dir))
        assert (Path(tmp_dir) / "outputVar.json").exists()
        with patch.object(jf, "notebook_output", True):
            capture_output = io.StringIO()
            sys.stdout = capture_output
            _ = jf.write_var_json(df, False, Path(tmp_dir))
            sys.stdout = sys.__stdout__
            assert "was successfully written and saved to " in capture_output.getvalue()

    var_dict = jf.write_var_json(df, False)
    assert "outputVar.json" in var_dict

    var_mlflow_dict = jf.write_var_json(input_dict, True)
    assert "inputVar.json" in var_mlflow_dict


def test_truncate_properties():
    """
    Test Cases:
    - Normal sized key and value
    - Key too big
    - Value too big
    - Key and value too big
    """
    test_property = {"test_key": "test_value"}
    assert test_property == jf.truncate_properties(test_property)

    big_key = "A" * 100
    big_value = "a" * 1000
    with pytest.warns(
        UserWarning,
        match=f"WARNING: The property name {big_key} was truncated to 60 characters.",
    ):
        test_property = {big_key: "test_value"}
        assert {big_key[:60]: "test_value"} == jf.truncate_properties(test_property)

    with pytest.warns(
        UserWarning,
        match=f"WARNING: The property value {big_value} was truncated to 512 "
        f"characters.",
    ):
        test_property = {"test_key": big_value}
        assert {"test_key": big_value[:512]} == jf.truncate_properties(test_property)

    with warnings.catch_warnings(record=True) as w:
        test_property = {big_key: big_value}
        assert {big_key[:60]: big_value[:512]} == jf.truncate_properties(test_property)
        assert len(w) == 2


def test_write_model_properties_json():
    """
    Test Cases:
    - File exists if json_path
    - Dict return if not json_path
    - Prediction model
    - Binary classification model
    - Multiclassification model
    - Improper number of target values
    - Truncate model description that is too long
    - Truncate custom property that is too long
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.object(jf, "notebook_output", True):
            capture_output = io.StringIO()
            sys.stdout = capture_output
            jf.write_model_properties_json(
                model_name="Test_Model",
                target_variable="BAD",
                target_values=[1, 0],
                json_path=Path(tmp_dir),
            )
            sys.stdout = sys.__stdout__
            assert (Path(tmp_dir) / "ModelProperties.json").exists()
            assert "was successfully written and saved to " in capture_output.getvalue()

    prop_dict = jf.write_model_properties_json(
        model_name="Test_Model",
        target_variable="BAD",
        target_values=None,
    )
    assert "ModelProperties.json" in prop_dict
    assert json.loads(prop_dict["ModelProperties.json"])["function"] == "Prediction"
    assert json.loads(prop_dict["ModelProperties.json"])["targetLevel"] == "Interval"
    assert json.loads(prop_dict["ModelProperties.json"])["targetEvent"] == ""
    assert json.loads(prop_dict["ModelProperties.json"])["eventProbVar"] == ""

    prop_dict = jf.write_model_properties_json(
        model_name="Test_Model", target_variable="BAD", target_values=[1, 0]
    )
    assert json.loads(prop_dict["ModelProperties.json"])["function"] == "Classification"
    assert json.loads(prop_dict["ModelProperties.json"])["targetLevel"] == "Binary"
    assert json.loads(prop_dict["ModelProperties.json"])["targetEvent"] == "1"
    assert json.loads(prop_dict["ModelProperties.json"])["eventProbVar"] == "P_1"

    prop_dict = jf.write_model_properties_json(
        model_name="Test_Model",
        target_variable="BAD",
        target_values=[4, 3, 1, 5],
    )
    assert json.loads(prop_dict["ModelProperties.json"])["targetLevel"] == "Nominal"
    assert json.loads(prop_dict["ModelProperties.json"])["properties"] == [
        {"name": "multiclass_target_events", "value": "4, 3, 1, 5", "type": "string"},
        {
            "name": "multiclass_proba_variables",
            "value": "P_4, P_3, P_1, P_5",
            "type": "string",
        },
    ]

    with pytest.warns():
        prop_dict = jf.write_model_properties_json(
            model_name="Test_Model", target_variable="BAD", model_desc="a" * 10000
        )
        assert len(json.loads(prop_dict["ModelProperties.json"])["description"]) <= 1024

    with pytest.raises(ValueError):
        prop_dict = jf.write_model_properties_json(
            model_name="Test_Model", target_variable="BAD", target_values=[1]
        )


def test_write_file_metadata_json():
    """
    Test cases:
    - Generate correctly named file with json_path provided
    - Return correctly labelled dict when no json_path is provided
    - Proper score resource name for H2O.ai model
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.object(jf, "notebook_output", True):
            capture_output = io.StringIO()
            sys.stdout = capture_output
            jf.write_file_metadata_json(
                model_prefix="Test_Model", json_path=Path(tmp_dir)
            )
            assert (Path(tmp_dir) / "fileMetadata.json").exists()
            sys.stdout = sys.__stdout__
            assert "was successfully written and saved to " in capture_output.getvalue()

    meta_dict = jf.write_file_metadata_json(model_prefix="Test_Model")
    assert "fileMetadata.json" in meta_dict

    meta_dict = jf.write_file_metadata_json(
        model_prefix="Test_Model", is_h2o_model=True
    )
    assert json.loads(meta_dict["fileMetadata.json"])[3]["name"] == "Test_Model.mojo"

    meta_dict = jf.write_file_metadata_json(
        model_prefix="Test_Model", is_tf_keras_model=True
    )
    assert json.loads(meta_dict["fileMetadata.json"])[3]["name"] == "Test_Model.h5"


def test_add_tuple_to_fitstat():
    """
    Test cases:
    - Raise error if non-tuple provided in list
    - Raise error if tuple provided has an invalid shape
    - Updated data_map is returned with a valid tuple list
    - Produce a warning if an invalid parameter is provided, but still complete function
    """
    invalid_tuple = 1
    tuple_list = [invalid_tuple]
    with pytest.raises(
        ValueError,
        match=f"Expected a tuple, but got {str(type(invalid_tuple))} instead.",
    ):
        jf.add_tuple_to_fitstat([], tuple_list)

    invalid_tuple = (1, 2, 3, 4, 5)
    tuple_list = [invalid_tuple]
    with pytest.raises(
        ValueError,
        match=f"Expected a tuple with three parameters, but instead got tuple with "
        f"length {len(invalid_tuple)} ",
    ):
        jf.add_tuple_to_fitstat([], tuple_list)

    jf.valid_params = ["_RASE_", "_NObs_"]
    data_map = [
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
    ]
    tuple_list = [("RASE", 10, 1), ("_NObs_", 33, "TEST")]
    new_map = jf.add_tuple_to_fitstat(data_map, tuple_list)
    assert new_map[0]["dataMap"]["_RASE_"] == 10
    assert new_map[1]["dataMap"]["_NObs_"] == 33

    invalid_param = "BAD"
    tuple_list.append((invalid_param, 0, 2))
    with pytest.warns(
        UserWarning,
        match=f"WARNING: {invalid_param} is not a valid parameter and has been ignored.",
    ):
        warn_map = jf.add_tuple_to_fitstat(data_map, tuple_list)
        assert warn_map[0]["dataMap"]["_RASE_"] == 10
        assert warn_map[1]["dataMap"]["_NObs_"] == 33


def test_user_input_fitstat(monkeypatch):
    """
    Test cases:
    - Produce a warning for invalid parameters, then stop input
    - Produce a warning for invalid role, then stop input
    - Updated data_map is returned with inputted values
    """

    def mock_input(prompt):
        return next(input_value)

    monkeypatch.setattr("builtins.input", mock_input)
    data_map = [
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
    ]
    jf.valid_params = ["_RASE_", "_NObs_"]
    invalid_param = "BAD"
    with pytest.warns(UserWarning, match=f"{invalid_param} is not a valid parameter."):
        input_value = iter([invalid_param, "Y", invalid_param, "N"])
        warn1_map = jf.user_input_fitstat(data_map)
        assert warn1_map == data_map

    invalid_role = 5
    with pytest.warns(
        UserWarning,
        match=f"{invalid_role} is not a valid role value. It should be either 1, 2, or "
        f"3 or TRAIN, TEST, or VALIDATE respectively.",
    ):
        input_value = iter(
            ["_NObs_", 10, invalid_role, "Y", "_NObs_", 10, invalid_role, "N"]
        )
        warn2_map = jf.user_input_fitstat(data_map)
        assert warn2_map == data_map

    input_value = iter(["_RASE_", 10, 1, "Y", "_NObs_", 33, "TEST", "N"])
    new_map = jf.user_input_fitstat(data_map)
    assert new_map[0]["dataMap"]["_RASE_"] == 10
    assert new_map[1]["dataMap"]["_NObs_"] == 33


def test_add_df_to_fitstat():
    """
    Test cases:
    - Produce warning for invalid parameter, but still return a data map
    - Produce warning for invalid role, but still return a data map
    - Updated data map is returned with valid values
    """
    data_map = [
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
        {"dataMap": {"_RASE_": None, "_NObs_": None}},
    ]
    invalid_param = "BAD"
    jf.valid_params = ["_RASE_", "_NObs_"]
    df = pd.DataFrame(data=[[invalid_param, 10, 1]])
    with pytest.warns(UserWarning, match=f"{invalid_param} is not a valid parameter."):
        warn1_map = jf.add_df_to_fitstat(df, data_map)
        assert data_map == warn1_map

    invalid_role = 5
    with pytest.warns(
        UserWarning,
        match=f"{invalid_role} is not a valid role value. It should be either 1, "
        f"2, or 3 or TRAIN, TEST, or VALIDATE respectively.",
    ):
        df = pd.DataFrame(data=[["_NObs_", 10, invalid_role]])
        warn2_map = jf.add_df_to_fitstat(df, data_map)
        assert warn2_map == data_map

    df = pd.DataFrame(data=[["_RASE_", 10, 1], ["_NObs_", 33, "TEST"]])
    new_map = jf.add_df_to_fitstat(df, data_map)
    assert new_map[0]["dataMap"]["_RASE_"] == 10
    assert new_map[1]["dataMap"]["_NObs_"] == 33


def test_input_fit_statistics(monkeypatch):
    """
    Test cases:
    - Generate correctly named file with json_path provided (tuple list)
    - Return correctly labelled dict when no json_path is provided (tuple list)
    - Repeat above with user input
    - Repeat above with input Dataframe
    """
    tuple_list = [("RASE", 10, 1), ("_NObs_", 33, "TEST")]
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.object(jf, "notebook_output", True):
            capture_output = io.StringIO()
            sys.stdout = capture_output
            jf.input_fit_statistics(tuple_list=tuple_list, json_path=Path(tmp_dir))
            assert (Path(tmp_dir) / "dmcas_fitstat.json").exists()
            sys.stdout = sys.__stdout__
            assert "was successfully written and saved to " in capture_output.getvalue()

    fitstat_dict = jf.input_fit_statistics(tuple_list=tuple_list)
    assert "dmcas_fitstat.json" in fitstat_dict

    fitstat_dict = jf.input_fit_statistics(fitstat_df=pd.DataFrame(data=tuple_list))
    assert "dmcas_fitstat.json" in fitstat_dict

    def mock_input(prompt):
        return next(input_value)

    monkeypatch.setattr("builtins.input", mock_input)
    input_value = iter(["_RASE_", 10, 1, "Y", "_NObs_", 33, "TEST", "N"])
    fitstat_dict = jf.input_fit_statistics(user_input=True)
    assert "dmcas_fitstat.json" in fitstat_dict


def test_check_for_data():
    """
    Test cases:
    - Raise error when no data provided
    - Check bool returns for different data arguments
    """
    with pytest.raises(
        ValueError,
        match="No data was provided. Please provide the actual and predicted values "
        r"for at least one of the partitions \(VALIDATE, TRAIN, or TEST\).",
    ):
        jf.check_for_data(None, None, None)

    assert jf.check_for_data(1, 2, 3) == [1, 1, 1]
    assert jf.check_for_data(1, None, None) == [1, 0, 0]


def test_stat_dataset_to_dataframe():
    """
    Test cases:
    - Raise ValueError if improper data is provided
    - Convert data from each type to proper dataframe
    - Create normalized probabilities for 2 column inputs (verify p E [0,1])
    """
    with pytest.raises(
        ValueError,
        match="Please provide the data in a list of lists, dataframe, or numpy "
        "array.",
    ):
        jf.stat_dataset_to_dataframe((1, 2), 1)

    dummy_target_value = 5
    dummy_actual = [float(random.randint(0, 10)) for _ in range(10)]
    dummy_predict = [float(random.randint(0, 10)) for _ in range(10)]
    dummy_proba = [x / max(dummy_predict) for x in dummy_predict]

    expected = pd.DataFrame(
        {"actual": dummy_actual, "predict": dummy_predict, "predict_proba": dummy_proba}
    )
    expected_binary = expected.drop(["predict_proba"], axis=1)
    expected_binary["predict_proba"] = (
        expected["predict"].gt(dummy_target_value).astype(int)
    )
    assert expected_binary["predict_proba"].between(0, 1).any()

    # pandas DataFrame
    df = pd.DataFrame({"a": dummy_actual, "p": dummy_predict, "pr": dummy_proba})
    assert jf.stat_dataset_to_dataframe(df).equals(expected)
    assert jf.stat_dataset_to_dataframe(
        df.drop(["predict_proba"], axis=1), dummy_target_value
    ).equals(expected_binary)

    # list of lists
    ll = [dummy_actual, dummy_predict, dummy_proba]
    assert jf.stat_dataset_to_dataframe(ll).equals(expected)
    assert jf.stat_dataset_to_dataframe(ll[0:2], dummy_target_value).equals(
        expected_binary
    )

    # numpy array
    array = np.array(ll)
    assert jf.stat_dataset_to_dataframe(array).equals(expected)
    assert jf.stat_dataset_to_dataframe(array[0:2], "5").equals(expected_binary)


def test_convert_data_role():
    """
    Test Cases:
    - Character to numeric (1, 2, 3)
        - int
        - float
        - invalid numeric
    - Numeric to character (TRAIN, TEST, VALIDATE)
        - lowercase
        - uppercase
        - invalid
    - Neither numeric nor character
    """
    test_data = [1, 2.0, 3.00, 5, "train", "TeSt", "VALIDATE", "other", (1, 2)]
    expected = ["TRAIN", "TEST", "VALIDATE", "TRAIN", 1, 2, 3, 1, 1]
    for data, exp in zip(test_data, expected):
        assert jf.convert_data_role(data) == exp


def test_get_pickle_file():
    """
    Test Cases:
    - Single pickle file in path (.pickle)
    - Multiple pickle files in path (.pickle and .pkl)
    - No pickle files in path
    """
    tmp_dir = tempfile.TemporaryDirectory()
    assert jf.get_pickle_file(Path(tmp_dir.name)) == []
    for suffix in [".json", ".json", ".py", ".json"]:
        _ = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=Path(tmp_dir.name)
        )
    pickle_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pickle", dir=Path(tmp_dir.name)
    )
    unittest.TestCase().assertCountEqual(
        first=[Path(pickle_file.name)], second=jf.get_pickle_file(Path(tmp_dir.name))
    )
    pkl_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pkl", dir=Path(tmp_dir.name)
    )
    pkl_file2 = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pkl", dir=Path(tmp_dir.name)
    )
    assert len(jf.get_pickle_file(Path(tmp_dir.name))) == 3
    unittest.TestCase().assertCountEqual(
        first=[Path(pickle_file.name), Path(pkl_file.name), Path(pkl_file2.name)],
        second=jf.get_pickle_file(Path(tmp_dir.name)),
    )


def test_get_pickle_dependencies(sklearn_classification_model):
    """
    Test Cases:
    - Return list of modules from sklearn model
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        with open(Path(tmp_dir) / "test.pickle", "wb") as f:
            pickle.dump(sklearn_classification_model, f)
        modules = jf.get_pickle_dependencies(Path(tmp_dir) / "test.pickle")
    expected = ["numpy", "sklearn"]
    unittest.TestCase().assertCountEqual(modules, expected)


def test_get_code_dependencies(change_dir):
    """
    Test Cases:
    - Return list of modules from example hmeq decision tree classifier model
    """
    change_dir("examples")

    modules = jf.get_code_dependencies(
        Path.cwd() / "data/hmeqModels/DecisionTreeClassifier"
    )
    expected = ["pandas", "pickle", "pathlib", "math", "numpy"]
    unittest.TestCase().assertCountEqual(modules, expected)


def test_remove_standard_library_packages():
    """
    Test Cases:
    - Remove standard library package from list
    """
    assert jf.remove_standard_library_packages(["math", "gc", "numpy"]) == ["numpy"]


def test_get_local_package_version():
    """
    Test Cases:
    - Generate a warning for a package not found
    - Return list with packages and versions from local environment
    """
    modules = ["numpy", "DNE_Package"]
    with pytest.warns(Warning):
        modules_versions = jf.get_local_package_version(modules)
        unittest.TestCase().assertCountEqual(
            modules_versions, [["numpy", np.__version__], ["DNE_Package", None]]
        )


def test_create_requirements_json(change_dir):
    """
    Test Cases:
    - Output requirements.json file if output_path is provided
    - Return list of dicts if output_path is None
        - Verify expected values returned in dicts
    """
    sk = pytest.importorskip("sklearn")
    change_dir("examples")

    example_model = (Path.cwd() / "data/hmeqModels/DecisionTreeClassifier").resolve()
    with tempfile.TemporaryDirectory() as tmp_dir:
        jf.create_requirements_json(example_model, Path(tmp_dir))
        assert (Path(tmp_dir) / "requirements.json").exists()

    json_dict = jf.create_requirements_json(example_model)
    assert "requirements.json" in json_dict
    expected = [
        {"step": "install pandas", "command": f"pip install pandas=={pd.__version__}"},
        {"step": "install numpy", "command": f"pip install numpy=={np.__version__}"},
        {
            "step": "install sklearn",
            "command": f"pip install sklearn=={sk.__version__}",
        },
    ]
    unittest.TestCase.maxDiff = None
    unittest.TestCase().assertCountEqual(
        json.loads(json_dict["requirements.json"]), expected
    )
