workflow auto {
  findStates(params, meta.config)
    | meta.workflow.run(
      auto: [publish: "state"]
    )
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------
// Method components (each may be instantiated with several variant/arch/seed
// argument sets — see the run-spec expansion in run_wf below).
method_components = [
  pca_reconstruction,
  autoencoder,
  scvi,
]

// Control methods (run once, receive the solution).
control_components = [
  ground_truth,
  negative_control,
]

// All components that can produce a prediction.
methods = method_components + control_components

// Metric components. All three score every method run. `biological` and
// `knn_purity` degrade to NA for the sub-metrics whose inputs an observational
// dataset (e.g. LuCA) lacks (DEG/cytokine/perturbation pools), so they never
// crash the benchmark.
metrics = [
  statistical,
  biological,
  knn_purity,
]

// ---------------------------------------------------------------------------
// Benchmark grid
// ---------------------------------------------------------------------------
// The six benchmarked models map onto two Viash components via arguments:
//   autoencoder: library_size_mode in {none, observed, modeled} -> AE/olAE/mlAE
//   scvi:        variant in {scvi, mlscvi, nlscvi}
// `settings_key` is the model name used in optimal_settings.yaml.
model_variants = [
  [model_id: "ae",     component: "autoencoder", settings_key: "AE",     args: [library_size_mode: "none"]],
  [model_id: "olae",   component: "autoencoder", settings_key: "olAE",   args: [library_size_mode: "observed"]],
  [model_id: "mlae",   component: "autoencoder", settings_key: "mlAE",   args: [library_size_mode: "modeled"]],
  [model_id: "scvi",   component: "scvi",        settings_key: "scVI",   args: [variant: "scvi"]],
  [model_id: "mlscvi", component: "scvi",        settings_key: "mlscVI", args: [variant: "mlscvi"]],
  [model_id: "nlscvi", component: "scvi",        settings_key: "nlscVI", args: [variant: "nlscvi"]],
]

latent_sizes = [10, 32, 128, 512, 2048]
seeds        = [0, 1, 2, 3, 4]

// Derive the split id (split01/02/03) for a dataset from its uns.
def resolveSplit(dataset_uns) {
  if (dataset_uns?.split) {
    return dataset_uns.split as String
  }
  // fall back to a split token inside the dataset_id (e.g. luca_split02)
  def did = (dataset_uns?.dataset_id ?: "") as String
  def m = (did =~ /(split0[0-9])/)
  return m ? m.group(1) : "split01"
}

workflow run_wf {
  take:
  input_ch

  main:

  // Load the tuned optimal architectures (model|latent|split -> arch).
  // optimal_settings.yaml is bundled as a workflow resource (see config).
  def optimal = readYaml(meta.resources_dir.resolve("optimal_settings.yaml")).optimal_settings

  /****************************
   * EXTRACT DATASET METADATA *
   ****************************/
  dataset_ch = input_ch
    | map { id, state ->
      [id, state + ["_meta": [join_id: id]]]
    }
    | extract_uns_metadata.run(
      fromState: [input: "input_solution"],
      toState: { id, output, state ->
        state + [dataset_uns: readYaml(output.output).uns]
      }
    )

  /***********************************************
   * EXPAND INTO PER-(model,latent,seed) RUN SPECS *
   ***********************************************/
  // Each spec resolves the optimal architecture for this dataset's split and
  // stamps the fixed component args (including the seed) and a stable method_id
  // that is shared across seeds so downstream aggregation averages over seeds.
  method_run_ch = dataset_ch
    | flatMap { id, state ->
      def split = resolveSplit(state.dataset_uns)
      def specs = []

      // tuned model variants x latent x seed
      model_variants.each { mv ->
        latent_sizes.each { latent ->
          def key = "${mv.settings_key}|${latent}|${split}".toString()
          def arch = optimal[key]
          if (arch == null) {
            System.err.println("run_benchmark: no optimal setting for ${key}; skipping")
            return
          }
          seeds.each { seed ->
            def method_args = [:] + mv.args
            method_args.n_latent = latent
            method_args.seed = seed
            method_args.max_epochs = arch.max_epochs
            if (mv.component == "autoencoder") {
              method_args.hidden_widths = arch.hidden_widths
            } else {
              method_args.n_hidden = arch.n_hidden
              method_args.n_layers = arch.n_layers
              method_args.max_kl_weight = arch.max_kl_weight
            }
            // method_base_id is shared across seeds; run_id is unique per seed
            // so per-seed outputs/checkpoints never collide.
            def base_id = "${mv.model_id}_l${latent}".toString()
            def run_id  = "${id}.${base_id}_s${seed}".toString()
            specs << [run_id, state + [
              method_component: mv.component,
              method_base_id:   base_id,
              method_args:      method_args,
              seed:             seed,
            ]]
          }
        }
      }

      // controls: run once each (no latent/seed grid)
      control_components.each { comp ->
        def base_id = comp.config.name
        specs << ["${id}.${base_id}".toString(), state + [
          method_component: base_id,
          method_base_id:   base_id,
          method_args:      [:],
          seed:             null,
        ]]
      }

      // PCA baseline: run once per latent (n_components = latent), no seed grid
      latent_sizes.each { latent ->
        def base_id = "pca_l${latent}".toString()
        specs << ["${id}.${base_id}".toString(), state + [
          method_component: "pca_reconstruction",
          method_base_id:   base_id,
          method_args:      [n_components: latent],
          seed:             null,
        ]]
      }

      specs
    }

  /***************************
   * RUN METHODS AND METRICS *
   ***************************/
  score_ch = method_run_ch
    | runEach(
      components: methods,
      // only run the component this spec targets, and only when normalization matches
      filter: { id, state, comp ->
        def targets = state.method_component == comp.config.name
        def norm = state.dataset_uns.normalization_id
        def pref = comp.config.info.preferred_normalization
        def norm_check = norm == pref
        def method_check = !state.method_ids || state.method_ids.contains(state.method_base_id)
        targets && norm_check && method_check
      },
      // the spec already carries a unique id; keep it
      id: { id, state, comp -> id },
      fromState: { id, state, comp ->
        def args = [
          input_train: state.input_train,
          input_test: state.input_test,
        ] + (state.method_args ?: [:])
        if (comp.config.info.type == "control_method") {
          args.input_solution = state.input_solution
        }
        args
      },
      toState: { id, output, state, comp ->
        state + [
          method_id: state.method_base_id,
          method_output: output.output,
        ]
      }
    )
    | runEach(
      components: metrics,
      id: { id, state, comp -> id + "." + comp.config.name },
      // All three metrics take the standard (solution, prediction) pair.
      // statistical scores directly; biological computes the sub-metrics whose
      // inputs are present (cellcycle/coexpression/pathway) and reports the
      // rest (DEG/cytokine) as NA; knn_purity has no perturbation pools here so
      // it reports NA. The extra optional inputs (references, perturbation
      // pools, cytokine signatures) are deliberately not passed.
      fromState: [
        input_solution: "input_solution",
        input_prediction: "method_output",
      ],
      toState: { id, output, state, comp ->
        state + [
          metric_id: comp.config.name,
          metric_output: output.output,
        ]
      }
    )
    | extract_uns_metadata.run(
      key: "extract_scores",
      fromState: [input: "metric_output"],
      toState: { id, output, state ->
        // annotate each score with the base method id and seed so the
        // downstream results processing can average mean +/- std over seeds
        def score_uns = readYaml(output.output).uns
        score_uns.method_id = state.method_base_id
        if (state.seed != null) {
          score_uns.seed = state.seed
        }
        state + [score_uns: score_uns]
      }
    )
    | joinStates { ids, states ->
      def score_uns = states.collect { it.score_uns }
      def score_uns_yaml_blob = toYamlBlob(score_uns)
      def score_uns_file = tempFile("score_uns.yaml")
      score_uns_file.write(score_uns_yaml_blob)
      ["output", [output_scores: score_uns_file]]
    }

  /******************************
   * GENERATE OUTPUT YAML FILES *
   ******************************/
  meta_ch = dataset_ch
    | joinStates { ids, states ->
      def dataset_uns = states.collect { state ->
        def uns = state.dataset_uns.clone()
        uns.remove("normalization_id")
        uns
      }
      def dataset_uns_file = tempFile("dataset_uns.yaml")
      dataset_uns_file.write(toYamlBlob(dataset_uns))

      def method_configs_file = tempFile("method_configs.yaml")
      method_configs_file.write(toYamlBlob(methods.collect { it.config }))

      def metric_configs_file = tempFile("metric_configs.yaml")
      metric_configs_file.write(toYamlBlob(metrics.collect { it.config }))

      def viash_file = meta.resources_dir.resolve("_viash.yaml")
      def new_state = [
        output_dataset_info: dataset_uns_file,
        output_method_configs: method_configs_file,
        output_metric_configs: metric_configs_file,
        output_task_info: viash_file,
        _meta: states[0]._meta,
      ]
      ["output", new_state]
    }

  output_ch = score_ch
    | mix(meta_ch)
    | joinStates { ids, states ->
      [ids[0], states.inject([:]) { acc, m -> acc + m }]
    }

  emit:
  output_ch
}
