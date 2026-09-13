{% macro check_untested_models() %}
  {% if execute %}
    {% set untested_query %}
      SELECT DISTINCT model_id
      FROM {{ ref('elementary', 'dbt_models') }}
      WHERE model_id NOT IN (
        SELECT DISTINCT model_id
        FROM {{ ref('elementary', 'dbt_tests') }}
      )
    {% endset %}
    {% set results = run_query(untested_query) %}
    {% if results %}
      {% for row in results %}
        {{ log("WARNING: Model '" ~ row[0] ~ "' has no quality tests defined.", info=True) }}
      {% endfor %}
    {% endif %}
  {% endif %}
{% endmacro %}
