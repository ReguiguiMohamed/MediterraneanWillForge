{% test accepted_range(model, column_name, min_value=None, max_value=None) %}

select *
from {{ model }}
where {{ column_name }} is not null
  {% if min_value is not none and max_value is not none %}
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
  {% elif min_value is not none %}
  and {{ column_name }} < {{ min_value }}
  {% elif max_value is not none %}
  and {{ column_name }} > {{ max_value }}
  {% else %}
  and false
  {% endif %}

{% endtest %}
