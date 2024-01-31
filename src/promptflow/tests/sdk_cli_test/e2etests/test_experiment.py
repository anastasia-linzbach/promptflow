from pathlib import Path

import pytest
from time import sleep
from ruamel.yaml import YAML

from promptflow import PFClient
from promptflow._sdk._constants import ExperimentStatus, RunStatus
from promptflow._sdk._load_functions import load_common
from promptflow._sdk.entities._experiment import (
    CommandNode,
    Experiment,
    ExperimentData,
    ExperimentInput,
    ExperimentTemplate,
    FlowNode,
)
from promptflow._sdk._errors import RunOperationError

TEST_ROOT = Path(__file__).parent.parent.parent
EXP_ROOT = TEST_ROOT / "test_configs/experiments"
FLOW_ROOT = TEST_ROOT / "test_configs/flows"


yaml = YAML(typ="safe")


@pytest.mark.e2etest
@pytest.mark.usefixtures("setup_experiment_table")
class TestExperiment:

    def wait_for_experiment_terminated(self, client, experiment):
        while experiment.status in [ExperimentStatus.IN_PROGRESS, ExperimentStatus.QUEUING]:
            experiment = client._experiments.get(experiment.name)
            sleep(10)
        return experiment

    def test_experiment_from_template(self):
        template_path = EXP_ROOT / "basic-no-script-template" / "basic.exp.yaml"
        # Load template and create experiment
        template = load_common(ExperimentTemplate, source=template_path)
        experiment = Experiment.from_template(template)
        # Assert experiment parts are resolved
        assert len(experiment.nodes) == 2
        assert all(isinstance(n, FlowNode) for n in experiment.nodes)
        assert len(experiment.data) == 1
        assert isinstance(experiment.data[0], ExperimentData)
        assert len(experiment.inputs) == 1
        assert isinstance(experiment.inputs[0], ExperimentInput)
        # Assert type is resolved
        assert experiment.inputs[0].default == 1
        # Pop schema and resolve path
        expected = dict(yaml.load(open(template_path, "r", encoding="utf-8").read()))
        expected.pop("$schema")
        expected["data"][0]["path"] = (FLOW_ROOT / "web_classification" / "data.jsonl").absolute().as_posix()
        expected["nodes"][0]["path"] = (experiment._output_dir / "snapshots" / "main").absolute().as_posix()
        expected["nodes"][1]["path"] = (experiment._output_dir / "snapshots" / "eval").absolute().as_posix()
        experiment_dict = experiment._to_dict()
        assert experiment_dict["data"][0].items() == expected["data"][0].items()
        assert experiment_dict["nodes"][0].items() == expected["nodes"][0].items()
        assert experiment_dict["nodes"][1].items() == expected["nodes"][1].items()
        assert experiment_dict.items() >= expected.items()

    def test_experiment_from_template_with_script_node(self):
        template_path = EXP_ROOT / "basic-script-template" / "basic-script.exp.yaml"
        # Load template and create experiment
        template = load_common(ExperimentTemplate, source=template_path)
        experiment = Experiment.from_template(template)
        # Assert command node load correctly
        assert len(experiment.nodes) == 4
        expected = dict(yaml.load(open(template_path, "r", encoding="utf-8").read()))
        experiment_dict = experiment._to_dict()
        assert isinstance(experiment.nodes[0], CommandNode)
        assert isinstance(experiment.nodes[1], FlowNode)
        assert isinstance(experiment.nodes[2], FlowNode)
        assert isinstance(experiment.nodes[3], CommandNode)
        gen_data_snapshot_path = experiment._output_dir / "snapshots" / "gen_data"
        echo_snapshot_path = experiment._output_dir / "snapshots" / "echo"
        expected["nodes"][0]["code"] = gen_data_snapshot_path.absolute().as_posix()
        expected["nodes"][3]["code"] = echo_snapshot_path.absolute().as_posix()
        expected["nodes"][3]["environment_variables"] = {}
        assert experiment_dict["nodes"][0].items() == expected["nodes"][0].items()
        assert experiment_dict["nodes"][3].items() == expected["nodes"][3].items()
        # Assert snapshots
        assert gen_data_snapshot_path.exists()
        file_count = len(list(gen_data_snapshot_path.rglob("*")))
        assert file_count == 1
        assert (gen_data_snapshot_path / "generate_data.py").exists()
        # Assert no file exists in echo path
        assert echo_snapshot_path.exists()
        file_count = len(list(echo_snapshot_path.rglob("*")))
        assert file_count == 0

    def test_experiment_create_and_get(self):
        template_path = EXP_ROOT / "basic-no-script-template" / "basic.exp.yaml"
        # Load template and create experiment
        template = load_common(ExperimentTemplate, source=template_path)
        experiment = Experiment.from_template(template)
        client = PFClient()
        exp = client._experiments.create_or_update(experiment)
        assert len(client._experiments.list()) > 0
        exp_get = client._experiments.get(name=exp.name)
        assert exp_get._to_dict() == exp._to_dict()

    @pytest.mark.usefixtures("use_secrets_config_file", "recording_injection", "setup_local_connection")
    def test_experiment_start(self):
        template_path = EXP_ROOT / "basic-no-script-template" / "basic.exp.yaml"
        # Load template and create experiment
        template = load_common(ExperimentTemplate, source=template_path)
        experiment = Experiment.from_template(template)
        client = PFClient()
        exp = client._experiments.create_or_update(experiment)
        exp = client._experiments.start(exp.name)

        # Test the experiment in progress cannot be started.
        with pytest.raises(RunOperationError) as e:
            client._experiments.start(exp.name)
        assert f"Experiment {exp.name} is {exp.status}" in str(e.value)
        assert exp.status in [ExperimentStatus.IN_PROGRESS, ExperimentStatus.QUEUING]
        exp = self.wait_for_experiment_terminated(client, exp)
        # Assert main run
        assert len(exp.node_runs["main"]) > 0
        main_run = client.runs.get(name=exp.node_runs["main"][0]["name"])
        assert main_run.status == RunStatus.COMPLETED
        assert main_run.variant == "${summarize_text_content.variant_0}"
        assert main_run.display_name == "main"
        assert len(exp.node_runs["eval"]) > 0
        # Assert eval run and metrics
        eval_run = client.runs.get(name=exp.node_runs["eval"][0]["name"])
        assert eval_run.status == RunStatus.COMPLETED
        assert eval_run.display_name == "eval"
        metrics = client.runs.get_metrics(name=eval_run.name)
        assert "accuracy" in metrics

        # Test experiment restart
        exp = client._experiments.start(exp.name)
        exp = self.wait_for_experiment_terminated(client, exp)
        for name, runs in exp.node_runs.items():
            assert all([run["status"] == RunStatus.COMPLETED] for run in runs)

    @pytest.mark.usefixtures("use_secrets_config_file", "recording_injection", "setup_local_connection")
    def test_experiment_with_script_start(self):
        template_path = EXP_ROOT / "basic-script-template" / "basic-script.exp.yaml"
        # Load template and create experiment
        template = load_common(ExperimentTemplate, source=template_path)
        experiment = Experiment.from_template(template)
        client = PFClient()
        exp = client._experiments.create_or_update(experiment)
        exp = client._experiments.start(exp.name)
        exp = self.wait_for_experiment_terminated(client, exp)
        assert exp.status == ExperimentStatus.TERMINATED
        assert len(exp.node_runs) == 4
        for key, val in exp.node_runs.items():
            assert val[0]["status"] == RunStatus.COMPLETED, f"Node {key} run failed"

    def test_cancel_experiment(self):
        template_path = EXP_ROOT / "command-node-exp-template" / "basic-command.exp.yaml"
        # Load template and create experiment
        template = load_common(ExperimentTemplate, source=template_path)
        experiment = Experiment.from_template(template)
        client = PFClient()
        exp = client._experiments.create_or_update(experiment)
        exp = client._experiments.start(exp.name)
        assert exp.status in [ExperimentStatus.IN_PROGRESS, ExperimentStatus.QUEUING]
        sleep(10)
        client._experiments.stop(exp.name)
        exp = client._experiments.get(exp.name)
        assert exp.status == ExperimentStatus.TERMINATED