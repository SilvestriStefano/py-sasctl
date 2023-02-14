# Copyright (c) 2020, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path
from warnings import warn

import pandas as pd

from .._services.model_repository import ModelRepository as mr
from ..core import current_session

MAS_CODE_NAME = "dmcas_packagescorecode.sas"
CAS_CODE_NAME = "dmcas_epscorecode.sas"


class ScoreCode:
    @classmethod
    def write_score_code(
        cls,
        model_prefix,
        input_data,
        predict_method,
        metrics,
        model_file_name,
        pickle_type="pickle",
        model=None,
        predict_threshold=None,
        score_code_path=None,
        target_values=None,
        missing_values=False,
        score_cas=True,
        **kwargs,
    ):
        """
        Generates Python score code based on training data used to create the model
        object.

        If a score_code_path argument is included, then a Python file is written to disk
        and can be included in the zip archive that is imported or registered into the
        common model repository. If no path is provided, then a dictionary is returned
        with the relevant score code files as strings.

        Disclaimer: The score code that is generated is designed to be a working
        template for any Python model, but is not guaranteed to work out of the box for
        scoring, publishing, or validating the model.

        The following files are generated by this function:
        * '*_score.py'
            The Python score code file for the model.
        * 'dcmas_epscorecode.sas' (for SAS Viya 3.5 models)
            Python score code wrapped in DS2 and prepared for CAS scoring or publishing.
        * 'dmcas_packagescorecode.sas' (for SAS Viya 3.5 models)
            Python score code wrapped in DS2 and prepared for SAS Microanalytic Score
            scoring or publishing.

        Parameters
        ----------
        model_prefix : string
            The variable for the model name that is used when naming model files.
            (For example: hmeqClassTree + [Score.py || .pickle]).
        input_data : DataFrame or list of dicts
            The `DataFrame` object contains the training data, and includes only the
            predictor columns. The write_score_code function currently supports int(64),
            float(64), and string data types for scoring. Providing a list of dict
            objects signals that the model files are being created from an MLFlow model.
        predict_method : Python function
            The Python function used for model predictions. For example, if the model is
            a Scikit-Learn DecisionTreeClassifier, then pass either of the following:
            sklearn.tree.DecisionTreeClassifier.predict
            sklearn.tree.DecisionTreeClassifier.predict_proba
        metrics : string list
            The scoring metrics for the model. For classification models, it is assumed
            that the first value in the list represents the classification output. This
            function supports single- and multi-class classification models.
        model_file_name : string
            Name of the model file that contains the model.
        score_code_path : string, optional
            The local path of the score code file. The default is the current
            working directory.
        pickle_type : string, optional
            Indicator for the package used to serialize the model file to be uploaded to
            SAS Model Manager. The default value is `pickle`.
        model : str or dict, optional
            The name or id of the model, or a dictionary representation of
            the model. The default value is None and is only necessary for models that
            will be hosted on SAS Viya 3.5.
        predict_threshold : float, optional
            The prediction threshold for normalized probability metrics. Values are
            expected to be between 0 and 1. The default value is None.
        score_code_path : string or Path object, optional
            Path for output score code file(s) to be generated. If no value is supplied
            a dict is returned instead. The default value is None.
        target_values : list of strings, optional
            A list of target values for the target variable. This argument and the
            metrics argument dictate the handling of the predicted values from the
            prediction method. The default value is None.
        missing_values : boolean, optional
            Sets whether data handled by the score code will impute for missing values.
        score_cas : boolean, optional
            Sets whether models registered to SAS Viya 3.5 should be able to be scored
            and validated through both CAS and SAS Micro Analytic Service. If set to
            false, then the model will only be able to be scored and validated through
            SAS Micro Analytic Service. The default value is True.
        kwargs : dict, optional
            Other keyword arguments are passed to one of the following functions:
            * sasctl.pzmm.ScoreCode._write_imports(pickle_type, mojo_model=None,
              binary_h2o_model=None, binary_string=None)
            * sasctl.pzmm.ScoreCode._viya35_model_load(model_id, pickle_type,
              model_file_name, mojo_model=None, binary_h2o_model=None)
            * sasctl.pzmm.ScoreCode._viya4_model_load(pickle_type, model_file_name,
              mojo_model=None, binary_h2o_model=None)
            * sasctl.pzmm.ScoreCode._predict_method(predict_method, input_var_list,
              dtype_list=None, statsmodels_model=None)
            * sasctl.pzmm.ScoreCode._predictions_to_metrics(metrics, target_values=None,
              predict_threshold=None, h2o_model=None)
        """
        if isinstance(input_data, pd.DataFrame):
            # From the input dataframe columns, create a list of input variables,
            # then check for viability
            input_var_list = input_data.columns.to_list()
            cls._check_for_invalid_variable_names(input_var_list)
            input_dtypes_list = input_data.dtypes.astype(str).to_list()
        else:
            # For MLFlow models, extract the variables and data types
            input_var_list = [var["name"] for var in input_data]
            cls._check_for_invalid_variable_names(input_var_list)
            input_dtypes_list = [var["type"] for var in input_data]

        try:
            # For SAS Viya 3.5, either return an error or return the model UUID
            if current_session().version_info() == 3.5:
                model_id = cls._get_model_id(model)
            else:
                model_id = None
        except AttributeError:
            model_id = None
            warn("No current session connection was found to a SAS Viya server. Score "
                 "code will be written under the assumption that the target server is "
                 "SAS Viya 4.")

        # Set the model_file_name based on kwargs input
        if "model_file_name" in kwargs:
            model_file_name = kwargs["model_file_name"]
            binary_string = None
        elif "binary_string" in kwargs:
            model_file_name = None
            binary_string = kwargs["binary_string"]
        else:
            binary_string = None

        # Add the core imports to the score code with the specified model serializer
        cls._write_imports(
            pickle_type,
            mojo_model="mojo_model" in kwargs,
            binary_h2o_model="binary_h2o_model" in kwargs,
            binary_string=binary_string,
        )

        # Generate model loading code for SAS Viya 3.5 models without binary strings
        if model_id and not binary_string:
            model_load = cls._viya35_model_load(
                model_id,
                pickle_type,
                model_file_name,
                mojo_model="mojo_model" in kwargs,
                binary_h2o_model="binary_h2o_model" in kwargs,
            )
        # As above, but for SAS Viya 4 models
        elif not binary_string:
            model_load = cls._viya4_model_load(
                pickle_type,
                model_file_name,
                mojo_model="mojo_model" in kwargs,
                binary_h2o_model="binary_h2o_model" in kwargs,
            )
        else:
            model_load = None

        # Define the score function using the variables found in input_data
        # Set the output variables in the line below from metrics
        cls.score_code += (
            f"def score{model_prefix}({', '.join(input_var_list)}):\n"
            f"{'':4}\"Output: {', '.join(metrics)}\"\n\n"
        )

        # Run a try/except block to catch errors for model loading (skip binary string)
        if model_load:
            cls.score_code += (
                f"{'':4}try:\n{'':8}global model\n{'':4}"
                f"except NameError:\n{model_load}"
            )

        if missing_values:
            cls._impute_missing_values(input_data, input_var_list, input_dtypes_list)

        # Create the appropriate style of input array and write out the predict method
        if any(x in ["mojo_model", "binary_h2o_model"] for x in kwargs):
            cls._predict_method(
                predict_method, input_var_list, dtype_list=input_dtypes_list
            )
            cls._predictions_to_metrics(
                metrics,
                target_values=target_values,
                predict_threshold=predict_threshold,
                h2o_model=True,
            )
        else:
            cls._predict_method(
                predict_method,
                input_var_list,
                statsmodels_model="statsmodels_model" in kwargs,
            )
            cls._predictions_to_metrics(
                metrics,
                target_values=target_values,
                predict_threshold=predict_threshold,
            )

        if model_id:
            files = [
                {
                    "name": f"{model_prefix}_score.py",
                    "file": cls.score_code,
                    "role": "score",
                }
            ]
            cls.upload_and_copy_score_resources(model_id, files)
            mr.convert_python_to_ds2(model_id)
            if score_cas:
                model_contents = mr.get_model_contents(model_id)
                for file in model_contents:
                    if file.name == "score.sas":
                        mas_code = mr.get(f"models/{file.modelId}/contents/{file.id}")
                        cls.upload_and_copy_score_resources(
                            model_id,
                            [
                                {
                                    "name": MAS_CODE_NAME,
                                    "file": mas_code,
                                    "role": "score",
                                }
                            ],
                        )
                        cas_code = cls.convert_mas_to_cas(mas_code, model_id)
                        cls.upload_and_copy_score_resources(
                            model_id,
                            [
                                {
                                    "name": CAS_CODE_NAME,
                                    "file": cas_code,
                                    "role": "score",
                                }
                            ],
                        )
                        model = mr.get_model(model_id)
                        model["scoreCodeType"] = "ds2MultiType"
                        mr.update_model(model)
                        break

        if score_code_path:
            py_code_path = Path(score_code_path) / (model_prefix + "_score.py")
            with open(py_code_path, "w") as py_file:
                py_file.write(cls.score_code)
            if model_id and score_cas:
                with open(Path(score_code_path) / MAS_CODE_NAME, "w") as sas_file:
                    # noinspection PyUnboundLocalVariable
                    sas_file.write(mas_code)
                with open(Path(score_code_path) / CAS_CODE_NAME, "w") as sas_file:
                    # noinspection PyUnboundLocalVariable
                    sas_file.write(cas_code)
        else:
            output_dict = {model_prefix + "_score.py": cls.score_code}
            if model_id and score_cas:
                # noinspection PyUnboundLocalVariable
                output_dict[MAS_CODE_NAME] = mas_code
                # noinspection PyUnboundLocalVariable
                output_dict[CAS_CODE_NAME] = cas_code
            return output_dict

    score_code = ""

    @staticmethod
    def upload_and_copy_score_resources(model, files):
        """
        Upload score resources to SAS Model Manager and copy them to the Compute server.

        Parameters
        ----------
        model : str or dict
            The name or id of the model, or a dictionary representation of the model.
        files : list of file objects
            The list of score resource files to upload.

        Returns
        -------
        RestObj
            API response to the call to copy resources to the Compute server.
        """
        for file in files:
            mr.add_model_content(model, **file)
        return mr.copy_python_resources(model)

    @staticmethod
    def _get_model_id(model):
        """
        Get the model uuid from SAS Model Manager.

        Parameters
        ----------
        model : str or dict
            The name or id of the model, or a dictionary representation of the model.

        Returns
        -------
        model_id : str
            UUID representation of the model from SAS Model Manager.
        """
        if not model:
            raise ValueError(
                "No model identification was provided. Python score code"
                " generation for SAS Viya 3.5 requires the model's UUID."
            )
        else:
            model_response = mr.get_model(model)
            try:
                model_id = model_response["id"]
            except TypeError:
                raise ValueError(
                    "No model could be found using the model argument provided."
                )
        return model_id

    @staticmethod
    def _check_for_invalid_variable_names(var_list):
        """
        Check for invalid variable names in the input dataset.

        Input data predictors must be valid Python variable names in order for the score
        code to be executed.

        Parameters
        ----------
        var_list : list of strings
            A list of strings pulled from the input dataset.

        Raises
        ------
        SyntaxError
            If an invalid variable name is supplied.
        """
        invalid_variables = []
        for name in var_list:
            if not str(name).isidentifier():
                invalid_variables.append(str(name))

        if len(invalid_variables) > 0:
            raise SyntaxError(
                f"The following are not valid variable names: "
                f"{', '.join(invalid_variables)}. Please confirm that all variable names"
                f" can be used as Python variables. E.g. `str(name).isidentifier() == "
                f"True`."
            )

    @classmethod
    def _write_imports(
        cls,
        pickle_type=None,
        mojo_model=False,
        binary_h2o_model=False,
        binary_string=None,
    ):
        """
        Write the import section of the Python score code.

        The session connection to SAS Viya is utilized to determine if the settings
        package used solely in SAS Viya 4 is needed.

        Parameters
        ----------
        pickle_type : string, optional
            Indicator for the package used to serialize the model file to be uploaded to
            SAS Model Manager. The default value is `pickle`.
        mojo_model : boolean, optional
            Flag to indicate that the model is a H2O.ai MOJO model. The default value is
            None.
        binary_h2o_model : boolean, optional
            Flag to indicate that the model is a H2O.ai binary model. The default value
            is None.
        binary_string : binary string, optional
            A binary representation of the Python model object. The default value is
            None.
        """
        pickle_type = pickle_type if pickle_type else "pickle"
        cls.score_code += (
            f"import math\nimport {pickle_type}\nimport pandas as pd\n"
            "import numpy as np\nfrom pathlib import Path\n\n"
        )

        try:
            if current_session().version_info() != 3.5:
                cls.score_code += "import settings\n\n"
        except AttributeError:
            warn("No current session connection was found to a SAS Viya server. Score "
                 "code will be written under the assumption that the target server is "
                 "SAS Viya 4.")

        if mojo_model or binary_h2o_model:
            cls.score_code += (
                "import h2o\nimport gzip\nimport shutil\nimport os\n\nh2o.init()\n\n"
            )
        elif binary_string:
            cls.score_code += (
                f'import codecs\n\nbinary_string = "{binary_string}"'
                f"\nmodel = {pickle_type}.loads(codecs.decode(binary_string"
                '.encode(), "base64"))\n\n'
            )

    @classmethod
    def _viya35_model_load(
        cls,
        model_id,
        model_file_name,
        pickle_type=None,
        mojo_model=False,
        binary_h2o_model=False,
    ):
        """
        Write the model load section of the score code assuming the model is being
        uploaded to SAS Viya 3.5.

        Parameters
        ----------
        model_id : string
            UUID representation of the model from SAS Model Manager.
        model_file_name : string
            Name of the model file that contains the model.
        pickle_type : string, optional
            Indicator for the package used to serialize the model file to be uploaded to
            SAS Model Manager. The default value is `pickle`.
        mojo_model : boolean, optional
            Flag to indicate that the model is a H2O.ai MOJO model. The default value is
            None.
        binary_h2o_model : boolean, optional
            Flag to indicate that the model is a H2O.ai binary model. The default value
            is None.

        Returns
        -------
        string
            Preformatted string for the next section of score code.
        """
        pickle_type = pickle_type if pickle_type else "pickle"

        if mojo_model:
            cls.score_code += (
                f'model_path = Path("/models/resources/viya/{model_id}'
                f'")\nwith gzip.open(model_path / "{model_file_name}'
                f'", "r") as fileIn, open(model_path / '
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\","
                f" \"wb\") as fileOut:\n{'':4}shutil.copyfileobj(fileIn,"
                " fileOut)\nos.chmod(model_path / "
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\""
                ", 0o777)\nmodel = h2o.import_mojo(model_path / "
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\")"
                "\n\n"
            )
            return (
                f"{'':8}model = h2o.import_mojo(model_path / \""
                f"{str(Path(model_file_name).with_suffix('.zip'))}\")"
            )
        elif binary_h2o_model:
            cls.score_code += (
                'model = h2o.load(Path("/models/resources/viya/'
                f'{model_id}/{model_file_name}"))\n\n'
            )
            return (
                f'        model = h2o.load(Path("/models/resources/viya/'
                f'{model_id}/{model_file_name}"))'
            )
        else:
            cls.score_code += (
                f'model_path = Path("/models/resources/viya/{model_id}'
                f'")\nwith open(model_path / "{model_file_name}", '
                f"\"rb\") as pickle_model:\n{'':4}model = {pickle_type}"
                ".load(pickle_model)\n\n"
            )
            return (
                f"{'':8}model_path = Path(\"/models/resources/viya/{model_id}"
                f"\")\n{'':8}with open(model_path / \"{model_file_name}\", "
                f"\"rb\") as pickle_model:\n{'':12}model = {pickle_type}"
                ".load(pickle_model)"
            )

    @classmethod
    def _viya4_model_load(
        cls, model_file_name, pickle_type=None, mojo_model=False, binary_h2o_model=False
    ):
        """
        Write the model load section of the score code assuming the model is being
        uploaded to SAS Viya 4.

        Parameters
        ----------
        model_file_name : string
            Name of the model file that contains the model.
        pickle_type : string, optional
            Indicator for the package used to serialize the model file to be uploaded to
            SAS Model Manager. The default value is `pickle`.
        mojo_model : boolean, optional
            Flag to indicate that the model is a H2O.ai MOJO model. The default value is
            None.
        binary_h2o_model : boolean, optional
            Flag to indicate that the model is a H2O.ai binary model. The default value
            is None.
        """
        pickle_type = pickle_type if pickle_type else "pickle"

        if mojo_model:
            cls.score_code += (
                f"with gzip.open(Path(settings.pickle_path) / "
                '"{model_file_name}", "r") as fileIn, '
                "open(Path(settings.pickle_path) / "
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\","
                f" \"wb\") as fileOut:\n{'':4}shutil.copyfileobj(fileIn,"
                " fileOut)\nos.chmod(Path(settings.pickle_path) / "
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\""
                ", 0o777)\nmodel = h2o.import_mojo("
                "Path(settings.pickle_path) / "
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\")"
                "\n\n"
            )
            return (
                f"{'':8}model = h2o.import_mojo(Path(settings.pickle_path) / "
                f"\"{str(Path(model_file_name).with_suffix('.zip'))}\")\n\n"
            )
        elif binary_h2o_model:
            cls.score_code += "model = h2o.load(Path(settings.pickle_path))\n\n"
            return f"{'':8}model = h2o.load(Path(settings.pickle_path))\n\n"
        else:
            cls.score_code += (
                f"with open(Path(settings.pickle_path) / "
                f'"{model_file_name}", "rb") as pickle_model:\n    '
                f"model = {pickle_type}.load(pickle_model)\n\n"
            )
            return (
                f"{'':8}with open(Path(settings.pickle_path) / "
                f'"{model_file_name}", "rb") as pickle_model:\n    '
                f"{'':12}model = {pickle_type}.load(pickle_model)\n\n"
            )

    @classmethod
    def _impute_missing_values(cls, data, var_list, dtype_list):
        """
        Write the missing value imputation section of the score code. This section of
        the score code is optional.

        Parameters
        ----------
        data : pandas.DataFrame
            Input dataset for model training or predictions.
        var_list : list of strings
            List of variable names
        dtype_list : list of strings
            List of variable data types
        """
        for (var, dtype) in zip(var_list, dtype_list):
            # Split up between numeric and character variables
            if any(t in dtype for t in ["int", "float"]):
                cls._impute_numeric(data, var)
            else:
                cls._impute_char(var)
        cls.score_code += "\n"

    @classmethod
    def _impute_numeric(cls, data, var):
        """
        Write imputation statement for a single numeric variable.

        Parameters
        ----------
        data : pandas.DataFrame
            Input dataset for model training or predictions.
        var : string
            Name of the variable to impute values for.
        """
        # If binary values, then compute the mode instead of the mean
        if data[var].isin([0, 1]).all():
            cls.score_code += (
                f"{'':4}try:\n{'':8}if math.isnan({var}):\n"
                f"{'':12}{var} = {data[var].mode()[0]}\n"
                f"{'':4}except TypeError:\n{'':8}{var} = "
                f"{data[var].mode()[0]}\n"
            )
        else:
            cls.score_code += (
                f"{'':4}try:\n{'':8}if math.isnan({var}):\n"
                f"{'':12}{var} = {data[var].mean()}\n"
                f"{'':4}except TypeError\n{'':8}{var} = "
                f"{data[var].mean()}\n"
            )

    @classmethod
    def _impute_char(cls, var):
        """
        Write imputation statement for a single string variable.

        Parameters
        ----------
        var : string
            Name of the variable to impute values for.
        """
        # Replace non-string values with blank strings
        cls.score_code += (
            f"{'':4}try:\n{'':8}{var} = {var}.strip()\n{'':4}except "
            f"AttributeError:\n{'':8}{var} = \"\"\n"
        )

    @classmethod
    def _predict_method(
        cls, method, var_list, dtype_list=None, statsmodels_model=False
    ):
        """
        Write the model prediction section of the score code.

        Parameters
        ----------
        method : Python function
            The Python function used for model predictions.
        var_list : list of strings
            List of variable names.
        dtype_list : list of strings, optional
            List of variable data types. The default value is None.
        statsmodels_model : boolean, optional
            Flag to indicate that the model is a statsmodels model. The default value is
            False.

        Returns
        -------

        """
        column_names = ", ".join(f"\"{col}\"" for col in var_list)
        # H2O models
        if dtype_list:
            column_types = []
            for (var, dtype) in zip(var_list, dtype_list):
                if any(x in dtype for x in ["int", "float"]):
                    col_type = "numeric"
                else:
                    col_type = "string"
                column_types.append(f'"{var}" : "{col_type}"')
            cls.score_code += (
                f"{'':4}input_array = pd.DataFrame("
                f"[[{', '.join(var_list)}]],\n{'':31}columns=["
                f"{column_names}],\n{'':31}dtype=float,\n{'':31}"
                f"index=[0])\n{'':4}column_types = {{{column_types}}}\n"
                f"{'':4}h2o_array = h2o.H2OFrame(input_array, "
                f"column_types=column_types)\n{'':4}prediction = "
                f"model.{method.__name__}(h2o_array)\n{'':4}prediction"
                f" = h2o.as_list(prediction, use_pandas=False)\n"
            )
        # Statsmodels models
        elif statsmodels_model:
            cls.score_code += (
                f"{'':4}inputArray = pd.DataFrame("
                f"[[1.0, {', '.join(var_list)}]],\n{'':29}columns=["
                f"\"const\", {column_names}],\n{'':29}dtype=float)\n"
                f"{'':4}prediction = model.{method.__name__}"
                f"(input_array)\n"
            )
        else:
            cls.score_code += (
                f"{'':4}input_array = pd.DataFrame("
                f"[[{', '.join(var_list)}]],\n{'':30}columns=["
                f"{column_names}],\n{'':30}dtype=float)\n{'':4}"
                f"prediction = model.{method.__name__}(input_array)\n"
            )

    @classmethod
    def _predictions_to_metrics(
        cls, metrics, target_values=None, predict_threshold=None, h2o_model=False
    ):
        """
        Using the provided arguments, write in to the score code the method for handling
        the generated predictions.

        Errors are raised for improper combinations of metric and target_value lengths.

        Parameters
        ----------
        metrics : string list
            A list of strings corresponding to the outputs of the model to SAS Model
            Manager.
        target_values : string list, optional
            A list of target values for the target variable. The default value is None.
        predict_threshold : float, optional
            The prediction threshold for normalized probability metrics. Values are
            expected to be between 0 and 1. The default value is None.
        h2o_model : boolean, optional
            Flag to indicate that the model is an H2O.ai model. The default is False.
        """
        if len(metrics) == 1 and isinstance(metrics, list):
            # Flatten single valued list
            metrics = metrics[0]

        if not (target_values or predict_threshold):
            cls._no_targets_no_thresholds(metrics, h2o_model)
        elif not target_values and predict_threshold:
            raise ValueError(
                "A threshold was provided to interpret the prediction results, however "
                "a target value was not, therefore, a valid output cannot be generated."
            )
        elif len(target_values) > 1:
            cls._nonbinary_targets(metrics, target_values, h2o_model)
        elif len(target_values) == 1 and int(target_values[0]) == 1:
            cls._binary_target(metrics, predict_threshold, h2o_model)
        elif len(target_values) == 1 and int(target_values[0]) != 1:
            raise ValueError(
                "For non-binary target variables, please provide at least two target "
                "values."
            )

    @classmethod
    def _no_targets_no_thresholds(cls, metrics, h2o_model=None):
        """
        Handle prediction outputs where the prediction does not expect handling by the
        score code.

        Parameters
        ----------
        metrics : string list
            A list of strings corresponding to the outputs of the model to SAS Model
            Manager.
        h2o_model : boolean, optional
            Flag to indicate that the model is an H2O.ai model. The default is False.
        """
        if len(metrics) == 1 or isinstance(metrics, str):
            # Assume no probability output & predict function returns classification
            if h2o_model:
                cls.score_code += (
                    f"{'':4}{metrics} = prediction[1][0]\n\n{'':4}return {metrics}"
                )
            else:
                cls.score_code += (
                    f"{'':4}{metrics} = prediction\n\n{'':4}return {metrics}"
                )
        else:
            if h2o_model:
                cls.score_code += f"{'':4}{metrics[0]} = prediction[1][0]\n"
                for i in range(len(metrics) - 1):
                    cls.score_code += (
                        f"{'':4}{metrics[i + 1]} = prediction[1][{i + 1}]\n"
                    )
            else:
                # Assume predict call returns (classification, probabilities)
                cls.score_code += f"{'':4}{metrics[0]} = prediction[0]\n"
                for i in range(len(metrics) - 1):
                    cls.score_code += f"{'':4}{metrics[i + 1]} = prediction[{i + 1}]\n"
            cls.score_code += f"\n{'':4}return {', '.join(metrics)}"

    @classmethod
    def _binary_target(cls, metrics, threshold=None, h2o_model=None):
        """
        Handle binary model prediction outputs.

        Parameters
        ----------
        metrics : string list
            A list of strings corresponding to the outputs of the model to SAS Model
            Manager.
        threshold : float, optional
            The prediction threshold for normalized probability metrics. Values are
            expected to be between 0 and 1. The default value is None.
        h2o_model : boolean, optional
            Flag to indicate that the model is an H2O.ai model. The default is False.
        """
        # If a binary target value is provided, then classify the prediction
        if not threshold:
            # Set default threshold
            threshold = 0.5
        if len(metrics) == 1 or isinstance(metrics, str):
            if h2o_model:
                cls.score_code += (
                    f"{'':4}if prediction[1][2] > {threshold}:\n"
                    f"{'':8}{metrics} = 1\n{'':4}else:\n{'':8}"
                    f"{metrics} = 0\n\nreturn {metrics}"
                )
            else:
                cls.score_code += (
                    f"{'':4}if prediction > {threshold}:\n"
                    f"{'':8}{metrics} = 1\n{'':4}else:\n{'':8}"
                    f"{metrics} = 0\n\nreturn {metrics}"
                )
        elif len(metrics) == 2:
            if h2o_model:
                cls.score_code += (
                    f"{'':4}if prediction[1][2] > {threshold}:\n"
                    f"{'':8}{metrics[0]} = 1\n{'':4}else:\n{'':8}"
                    f"{metrics[0]} = 0\n\nreturn {metrics[0]}, "
                    f"prediction[1][2]"
                )
            else:
                cls.score_code += (
                    f"{'':4}if prediction > {threshold}:\n"
                    f"{'':8}{metrics[0]} = 1\n{'':4}else:\n{'':8}"
                    f"{metrics[0]} = 0\n\nreturn {metrics[0]}, prediction"
                )
        else:
            raise ValueError("Too many metrics were provided for a binary model.")

    @classmethod
    def _nonbinary_targets(cls, metrics, target_values, h2o_model=None):
        """
        Handle multiclass model prediction outputs.

        Parameters
        ----------
        metrics : string list
            A list of strings corresponding to the outputs of the model to SAS Model
            Manager.
        target_values : string list, optional
            A list of target values for the target variable. The default value is None.
        h2o_model : boolean, optional
            Flag to indicate that the model is an H2O.ai model. The default is False.
        """
        # Find the target value with the highest probability
        if len(metrics) == 1 or isinstance(metrics, str):
            if h2o_model:
                cls.score_code += (
                    f"{'':4}target_values = {target_values}\n{'':4}"
                    f"{metrics} = target_values[prediction[1][1:]."
                    f"index(max(prediction[1][1:]))]\n{'':4}"
                    f"return {metrics}"
                )
            else:
                cls.score_code += (
                    f"{'':4}target_values = {target_values}\n{'':4}"
                    f"{metrics} = target_values[prediction.index("
                    f"max(prediction))]\n{'':4}return {metrics}"
                )
        elif len(metrics) in (len(target_values), len(target_values) + 1):
            if h2o_model:
                cls.score_code += (
                    f"{'':4}target_values = {target_values}\n"
                )
                for i in range(len(metrics) - 1):
                    cls.score_code += (
                        f"{'':4}{metrics[i + 1]} = prediction[1][{i + 1}]\n"
                    )
                if len(metrics) == len(target_values) + 1:
                    cls.score_code += f"{'':4}{metrics[0]} = target_values" \
                                      f"[prediction[1][1:].index(max(" \
                                      f"prediction[1][1:]))]\n"
                    cls.score_code += f"{'':4}return {', '.join(metrics)}"
                else:
                    cls.score_code += f"{'':4}return {', '.join(metrics)}"
            else:
                cls.score_code += (
                    f"{'':4}target_values = {target_values}\n"
                )
                for i in range(len(metrics) - 1):
                    cls.score_code += f"{'':4}{metrics[i + 1]} = prediction[{i + 1}]\n"
                if len(metrics) == len(target_values) + 1:
                    cls.score_code += f"{'':4}{metrics[0]} = target_values" \
                                      f"[prediction.index(max(prediction))]\n"
                    cls.score_code += f"{'':4}return {', '.join(metrics)}"
                else:
                    cls.score_code += f"{'':4}return {', '.join(metrics)}"
        else:
            raise ValueError("An invalid number of target values were provided with "
                             "respect to the size of the metrics provided. The "
                             "function is expecting metrics to be one, the same length"
                             "as the target values list, or one more than the length"
                             "of the target values list.")

    @staticmethod
    def convert_mas_to_cas(mas_code, model):
        """
        Using the generated score.sas code from the Python wrapper API, convert the
        SAS Microanalytic Service based code to CAS compatible.

        Parameters
        ----------
        mas_code : str
            String representation of the dmcas_packagescorecode.sas DS2 wrapper
        model : str or dict
            The name or id of the model, or a dictionary representation of the model

        Returns
        -------
        CASCode : str
            String representation of the dmcas_epscorecode.sas DS2 wrapper code
        """
        model = mr.get_model(model)
        output_string = ""
        for out_var in model["outputVariables"]:
            output_string += "dcl "
            if out_var["type"] == "string":
                output_string = output_string + "varchar(100) "
            else:
                output_string += "double "
            output_string += out_var["name"] + ";\n"
        start = mas_code.find("score(")
        finish = mas_code[start:].find(");")
        score_vars = mas_code[start + 6: start + finish]
        input_string = " ".join(
            [
                x
                for x in score_vars.split(" ")
                if (x != "double" and x != "in_out" and x != "varchar(100)")
            ]
        )
        end_block = (
            f"method run();\n{'':4}set SASEP.IN;\n{'':4}score({input_string});\nend;"
            f"\nenddata;"
        )
        replace_strings = {
            "package pythonScore / overwrite=yes;": "data sasep.out;",
            "dcl int resultCode revision;": "dcl double resultCode revision;\n"
            + output_string,
            "endpackage;": end_block,
        }
        replace_strings = dict((re.escape(k), v) for k, v in replace_strings.items())
        pattern = re.compile("|".join(replace_strings.keys()))
        cas_code = pattern.sub(
            lambda m: replace_strings[re.escape(m.group(0))], mas_code
        )
        return cas_code
