use std::collections::BTreeMap;

use crate::{Entity, Name, Scene, UNMAPPED_CLASS};

/// Which class filters a decoder offers and their defaults.
#[derive(Clone, Debug, PartialEq)]
pub struct FilterPolicy {
    /// The decoder the policy belongs to.
    pub name: &'static str,
    /// The filters in the order they are shown.
    pub options: Vec<FilterOption>,
}

/// One filter the user can switch on or off.
#[derive(Clone, Debug, PartialEq)]
pub struct FilterOption {
    /// Stable id, such as `show_car`.
    pub id: Name,
    /// What the user reads.
    pub label: Name,
    /// Whether the filter starts switched on.
    pub default_enabled: bool,
    /// Which classifications the filter applies to.
    pub rule: ClassRule,
}

/// Selects observations by their class names.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClassRule {
    /// Class names the rule selects; a name starting with `?` selects every unmapped class.
    pub types: Vec<Name>,
    /// Whether an observation with no classes is selected.
    pub include_empty: bool,
    /// Whether the selection is inverted.
    pub exclude: bool,
}

impl ClassRule {
    /// Whether the rule selects an observation with these class names.
    ///
    /// An observation with no classes is selected when `include_empty` is set. Otherwise it is selected when any of
    /// its classes is: an unlisted name starting with `?` counts as the name `?`, and `exclude` inverts the test.
    /// An empty name is never selected, not even when inverted.
    pub fn matches<'a>(&self, types: impl IntoIterator<Item = &'a str>) -> bool {
        let mut any = false;
        for name in types {
            any = true;
            if self.matches_name(name) {
                return true;
            }
        }
        !any && self.include_empty
    }

    fn matches_name(&self, name: &str) -> bool {
        if name.is_empty() {
            return false;
        }
        let listed = |candidate: &str| self.types.iter().any(|t| t == candidate);
        let key = if !listed(name) && name.starts_with(UNMAPPED_CLASS) {
            UNMAPPED_CLASS
        } else {
            name
        };
        listed(key) != self.exclude
    }
}

/// The user's toggles as overrides of the policy defaults, by option id. Ids the policy lacks are kept, without
/// effect.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct FilterChoice {
    /// Whether each option is on, for the options the user switched away from their default.
    pub overrides: BTreeMap<String, bool>,
}

impl FilterPolicy {
    /// Whether the option is on: the user's override, else the option's default; off for an id the policy lacks.
    pub fn is_enabled(&self, choice: &FilterChoice, option: &str) -> bool {
        choice.overrides.get(option).copied().unwrap_or_else(|| {
            self.options
                .iter()
                .find(|candidate| candidate.id == option)
                .is_some_and(|candidate| candidate.default_enabled)
        })
    }

    /// Whether the entity stays.
    ///
    /// The entity is judged by the class names of its latest observation (none when it has no observation). It stays
    /// when an enabled option selects it; it goes when only disabled options select it, or when every option is
    /// disabled; it stays when no option selects it, so a class the policy does not know is never hidden by a
    /// toggle. A policy without options keeps everything.
    pub fn keeps(&self, choice: &FilterChoice, entity: &Entity) -> bool {
        self.verdict(|_, option| self.is_enabled(choice, &option.id), entity)
    }

    /// The entities of `scene` that stay, as a view; the Scene is never copied.
    ///
    /// Each option's state is read once, and the view is the whole Scene when every option is on.
    pub fn view<'a>(&self, choice: &FilterChoice, scene: &'a Scene) -> SceneView<'a> {
        let enabled: Vec<bool> = self
            .options
            .iter()
            .map(|option| self.is_enabled(choice, &option.id))
            .collect();
        if enabled.iter().all(|&on| on) {
            return SceneView::all(scene);
        }
        let entities = scene
            .entities()
            .iter()
            .enumerate()
            .filter(|(_, entity)| self.verdict(|index, _| enabled[index], entity))
            .filter_map(|(index, _)| u32::try_from(index).ok())
            .collect();
        SceneView { scene, entities }
    }

    /// The rule of [`FilterPolicy::keeps`] with the options' states supplied by `is_on`.
    fn verdict(&self, is_on: impl Fn(usize, &FilterOption) -> bool, entity: &Entity) -> bool {
        if self.options.is_empty() {
            return true;
        }
        let names = || {
            entity
                .latest_observation()
                .into_iter()
                .flat_map(|observation| &observation.classifications)
                .map(|classification| classification.class_type.as_str())
        };
        let mut selected_by_disabled = false;
        let mut any_enabled = false;
        for (index, option) in self.options.iter().enumerate() {
            let on = is_on(index, option);
            any_enabled |= on;
            if option.rule.matches(names()) {
                if on {
                    return true;
                }
                selected_by_disabled = true;
            }
        }
        any_enabled && !selected_by_disabled
    }
}

/// A filtered Scene by entity index. Relations are drawn only when both endpoints are in `entities`.
#[derive(Clone, Debug)]
pub struct SceneView<'a> {
    /// The whole Scene.
    pub scene: &'a Scene,
    /// Indices into `scene.entities()` of the entities that stay, ascending.
    pub entities: Vec<u32>,
}

impl<'a> SceneView<'a> {
    /// A view of every entity.
    pub fn all(scene: &'a Scene) -> Self {
        let count = u32::try_from(scene.entities().len()).unwrap_or(u32::MAX);
        Self {
            scene,
            entities: (0..count).collect(),
        }
    }

    /// The entities that stay, in Scene order.
    pub fn entities(&self) -> impl Iterator<Item = &'a Entity> {
        let scene = self.scene;
        self.entities
            .iter()
            .filter_map(move |&index| scene.entities().get(usize::try_from(index).ok()?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        BoundingBox, ClassType, Classification, EntityId, Geometry, Observation, SceneTime,
        TimeSlice,
    };

    fn rule(types: &[&'static str], include_empty: bool, exclude: bool) -> ClassRule {
        ClassRule {
            types: types.iter().map(|t| Name::Borrowed(*t)).collect(),
            include_empty,
            exclude,
        }
    }

    #[test]
    fn rules_select_observations_by_class_names() {
        // (rule, class names, expected)
        let cases = [
            (rule(&["car"], false, false), vec!["car"], true),
            (rule(&["car"], false, false), vec!["truck"], false),
            (rule(&["car"], false, false), vec!["truck", "car"], true),
            (rule(&["car"], false, false), vec![""], false),
            (rule(&["?"], false, false), vec!["?99"], true),
            (rule(&["?"], false, false), vec!["car"], false),
            (rule(&["?5"], false, false), vec!["?5"], true),
            (rule(&["?5"], false, false), vec!["?6"], false),
            (rule(&["car"], false, false), vec![], false),
            (rule(&["car"], true, false), vec![], true),
            (rule(&["car"], true, false), vec!["truck"], false),
            (rule(&["car"], false, true), vec!["truck"], true),
            (rule(&["car"], false, true), vec!["car"], false),
            (rule(&["car"], false, true), vec!["car", "truck"], true),
            (rule(&["?"], false, true), vec!["?7"], false),
            (rule(&["car"], false, true), vec![], false),
        ];
        for (rule, names, expected) in cases {
            assert_eq!(
                rule.matches(names.iter().copied()),
                expected,
                "{rule:?} on {names:?}"
            );
        }
    }

    fn option(id: &'static str, class: &'static str, enabled: bool) -> FilterOption {
        FilterOption {
            id: id.into(),
            label: id.into(),
            default_enabled: enabled,
            rule: rule(&[class], false, false),
        }
    }

    fn policy(options: Vec<FilterOption>) -> FilterPolicy {
        FilterPolicy {
            name: "test",
            options,
        }
    }

    fn entity(id: &str, classes: &[&str]) -> Entity {
        let geometry = Geometry::Box(BoundingBox::new(0.1, 0.1, 0.3, 0.3).unwrap());
        let mut observation = Observation::new(SceneTime::Frame(0), geometry);
        observation.classifications = classes
            .iter()
            .map(|class| Classification::new(ClassType::new(class.to_string()).unwrap()))
            .collect();
        let mut entity = Entity::new(EntityId::new(id).unwrap());
        entity.push_observation(observation);
        entity
    }

    fn scene(entities: Vec<Entity>) -> Scene {
        Scene::new(TimeSlice::at(SceneTime::Frame(0)))
            .with_entities(entities)
            .unwrap()
    }

    fn kept<'a>(policy: &FilterPolicy, choice: &FilterChoice, scene: &'a Scene) -> Vec<&'a str> {
        policy
            .view(choice, scene)
            .entities()
            .map(|entity| entity.id().as_str())
            .collect()
    }

    fn off(ids: &[&str]) -> FilterChoice {
        FilterChoice {
            overrides: ids.iter().map(|id| (id.to_string(), false)).collect(),
        }
    }

    #[test]
    fn an_entity_stays_by_enabled_options_and_goes_by_disabled_ones() {
        let policy = policy(vec![
            option("show_car", "car", true),
            option("show_bus", "bus", true),
        ]);
        let scene = scene(vec![
            entity("car", &["car"]),
            entity("bus", &["bus"]),
            entity("other", &["unexpected"]),
            entity("none", &[]),
        ]);
        let all = kept(&policy, &FilterChoice::default(), &scene);
        assert_eq!(all, ["car", "bus", "other", "none"]);
        // A class no option selects is kept, whatever is toggled.
        let no_cars = kept(&policy, &off(&["show_car"]), &scene);
        assert_eq!(no_cars, ["bus", "other", "none"]);
        // Every option disabled shows nothing, not even what no option selects.
        let nothing = kept(&policy, &off(&["show_car", "show_bus"]), &scene);
        assert!(nothing.is_empty());
        // An entity of two classes stays while one of its options is on.
        let mixed = self::scene(vec![entity("both", &["car", "bus"])]);
        assert_eq!(kept(&policy, &off(&["show_car"]), &mixed), ["both"]);
        assert!(kept(&policy, &off(&["show_car", "show_bus"]), &mixed).is_empty());
    }

    #[test]
    fn defaults_apply_and_overrides_win_in_both_directions() {
        let policy = policy(vec![
            option("shown", "car", true),
            option("hidden", "bus", false),
        ]);
        let choice = FilterChoice {
            overrides: [("hidden".to_owned(), true), ("shown".to_owned(), false)].into(),
        };
        let cases = [
            (FilterChoice::default(), "shown", true),
            (FilterChoice::default(), "hidden", false),
            (choice.clone(), "shown", false),
            (choice, "hidden", true),
            (FilterChoice::default(), "unknown", false),
        ];
        for (choice, id, expected) in cases {
            assert_eq!(
                policy.is_enabled(&choice, id),
                expected,
                "{id} under {choice:?}"
            );
        }
        let scene = scene(vec![entity("car", &["car"]), entity("bus", &["bus"])]);
        assert_eq!(kept(&policy, &FilterChoice::default(), &scene), ["car"]);
    }

    #[test]
    fn overrides_of_ids_the_policy_lacks_have_no_effect() {
        let policy = policy(vec![option("show_car", "car", true)]);
        let scene = scene(vec![entity("car", &["car"])]);
        let choice = off(&["show_truck"]);
        assert_eq!(kept(&policy, &choice, &scene), ["car"]);
        assert!(!policy.is_enabled(&choice, "show_truck"));
    }

    #[test]
    fn unmapped_classes_follow_the_question_mark_option() {
        let policy = policy(vec![
            option("show_car", "car", true),
            option("show_unmapped", "?", true),
        ]);
        let scene = scene(vec![entity("a", &["?7"]), entity("b", &["car"])]);
        assert_eq!(kept(&policy, &off(&["show_unmapped"]), &scene), ["b"]);
        assert_eq!(kept(&policy, &FilterChoice::default(), &scene), ["a", "b"]);
    }

    #[test]
    fn only_the_latest_observation_is_judged() {
        let policy = policy(vec![
            option("show_car", "car", false),
            option("show_bus", "bus", true),
        ]);
        let mut moving = entity("moving", &["car"]);
        let geometry = Geometry::Box(BoundingBox::new(0.2, 0.2, 0.4, 0.4).unwrap());
        let mut later = Observation::new(SceneTime::Frame(1), geometry);
        later.classifications = vec![Classification::new(ClassType::BUS)];
        moving.push_observation(later);
        let scene = scene(vec![moving]);
        assert_eq!(kept(&policy, &FilterChoice::default(), &scene), ["moving"]);
        let choice = off(&["show_bus"]);
        assert!(kept(&policy, &choice, &scene).is_empty());
        assert!(!policy.keeps(&choice, &scene.entities()[0]));
    }

    #[test]
    fn an_entity_without_observations_is_judged_with_no_classes() {
        let mut unknown = option("show_unknown", "unknown", true);
        unknown.rule.include_empty = true;
        let policy = policy(vec![unknown]);
        let bare = Entity::new(EntityId::new("bare").unwrap());
        assert!(policy.keeps(&FilterChoice::default(), &bare));
        assert!(!policy.keeps(&off(&["show_unknown"]), &bare));
    }

    #[test]
    fn a_policy_without_options_keeps_everything() {
        let scene = scene(vec![entity("a", &["car"])]);
        assert_eq!(
            kept(&policy(vec![]), &FilterChoice::default(), &scene),
            ["a"]
        );
    }
}
