import torch
from diffusers import BitsAndBytesConfig
from .quantization import quantize


class Pipeline:
    def __init__(self, pipeline_definition, default_seed):
        self.pipeline_definition = pipeline_definition
        self.default_seed = default_seed
        self.pipeline = None

    @property
    def model_name(self):
        return self.pipeline_definition["from_pretrained_arguments"]["model_name"]

    @property
    def argument_template(self):
        return self.pipeline_definition["arguments"]

    def load(self, device_identifier, shared_components):
        from_pretrained_arguments = self.pipeline_definition[
            "from_pretrained_arguments"
        ]

        # grab any previously shared components and put them into from_pretrained_arguments
        # if a pipeline asks for both a shared component and specifies a configuration for
        # that component, the configuration will take precedence
        for reused_component_name in self.pipeline_definition.get(
            "reused_components", []
        ):
            from_pretrained_arguments[reused_component_name] = shared_components[
                reused_component_name
            ]

        for optional_component_name in [
            "controlnet",
            "transformer",
            "vae",
            "unet",
            "text_encoder",
            "text_encoder_2",
            "tokenizer",
            "tokenizer_2",
            "image_encoder",
        ]:
            self.load_optional_component(
                optional_component_name, from_pretrained_arguments, device_identifier
            )

        # load and configure the pipeline
        pipeline = load_and_configure_pipeline(
            self.pipeline_definition["configuration"],
            from_pretrained_arguments,
            device_identifier,
        )

        # load and configure any custom scheduler
        load_and_configure_scheduler(
            self.pipeline_definition.get("scheduler", None), pipeline
        )

        # store any shared pipeline components for future use by other steps
        for shared_component_name in self.pipeline_definition.get(
            "shared_components", []
        ):
            shared_components[shared_component_name] = getattr(
                pipeline, shared_component_name
            )

        # load loras and fuse them into the pipeline
        load_loras(self.pipeline_definition.get("loras", []), pipeline)

        # load ip adapter
        load_ip_adapter(self.pipeline_definition.get("ip_adapter", None), pipeline)

        # create a generator that will be used by the pipeline
        if not "no_generator" in self.pipeline_definition["configuration"]:
            self.argument_template["generator"] = torch.Generator(
                device_identifier
            ).manual_seed(self.pipeline_definition.get("seed", self.default_seed))

        self.pipeline = pipeline

    @torch.inference_mode()
    def run(self, arguments):
        if self.pipeline is None:
            raise ValueError(
                "Pipeline has not been initialized. Call load(device_identifier, shared_components) first."
            )

        # run the pipeline
        pipeline_output = self.pipeline(**arguments)
        return pipeline_output

    def load_optional_component(
        self, component_name, from_pretrained_arguments, device_identifier
    ):
        component = load_and_configure_component(
            self.pipeline_definition.get(component_name, None),
            component_name,
            device_identifier,
        )
        if component is not None:
            from_pretrained_arguments[component_name] = component


def load_loras(loras, pipeline):
    for lora in loras:
        default_lora_scale = 1.0 / len(
            loras
        )  # default to equally distributing lora weights
        lora_name = lora.pop("lora_name", None)
        lora_scale = lora.pop("lora_scale", default_lora_scale)
        pipeline.load_lora_weights(lora_name, **lora)
        pipeline.fuse_lora(lora_scale=lora_scale)


def load_ip_adapter(ip_adapter_definition, pipeline):
    if ip_adapter_definition is not None:
        model_name = ip_adapter_definition.pop("model_name")
        scale = ip_adapter_definition.pop("ip_adapter_scale", None)
        pipeline.load_ip_adapter(model_name, **ip_adapter_definition)
        if scale is not None:
            pipeline.set_ip_adapter_scale(scale)


def load_and_configure_scheduler(scheduler_definition, pipeline):
    if scheduler_definition is not None:
        scheduler_configuration = scheduler_definition.get("configuration", None)
        from_config_args = scheduler_definition.get("from_config_args", {})
        scheduler_type = scheduler_configuration.get("scheduler_type", None)
        print(f"Loading scheduler {scheduler_type}...")

        pipeline.scheduler = scheduler_type.from_config(
            pipeline.scheduler.config, **from_config_args
        )


def load_and_configure_component(
    component_definition, component_name, device_identifier
):
    if component_definition is not None:
        print(f"Loading {component_name}...")
        component_configuration = component_definition["configuration"]
        component_from_pretrained_arguments = component_definition[
            "from_pretrained_arguments"
        ]
        component = load_and_configure_pipeline(
            component_configuration,
            component_from_pretrained_arguments,
            device_identifier,
        )
        return quantize(
            component, component_definition.get("quantization", None), device_identifier
        )

    return None


def load_and_configure_pipeline(
    configuration, from_pretrained_arguments, device_identifier
):
    bits_and_bytes_configuration = configuration.get(
        "bits_and_bytes_configuration", None
    )
    if bits_and_bytes_configuration is not None:
        print("Loading bits and bytes configuration")
        from_pretrained_arguments["quantization_config"] = BitsAndBytesConfig(
            **bits_and_bytes_configuration
        )

    # load the pipeline
    pipeline_type = configuration["pipeline_type"]
    model_name = from_pretrained_arguments.pop("model_name")
    print(f"Loading pipeline {model_name}...")

    pipeline = pipeline_type.from_pretrained(model_name, **from_pretrained_arguments)

    offload = configuration.get("offload", None)
    if offload == "full":
        pipeline.enable_model_cpu_offload()
    elif offload == "sequential":
        pipeline.enable_sequential_cpu_offload()
    else:
        pipeline = pipeline.to(device_identifier)

    vae = configuration.get("vae", {})
    if vae.get("enable_slicing", False):
        pipeline.vae.enable_slicing()
    if vae.get("enable_tiling", False):
        pipeline.vae.enable_tiling()
    if vae.get("set_memory_format", False):
        pipeline.vae.to(memory_format=torch.channels_last)

    unet = configuration.get("unet", {})
    if unet.get("enable_forward_chunking", False):
        pipeline.unet.enable_forward_chunking()
    if unet.get("set_memory_format", False):
        pipeline.unet.to(memory_format=torch.channels_last)

    return pipeline
