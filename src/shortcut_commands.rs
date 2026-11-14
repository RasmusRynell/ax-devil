//! The commands the shortcuts follow: the built-ins and those of the plugins that loaded.

use crate::plugins::Plugins;

/// The built-in commands and those of the plugins that loaded, with each plugin default key that is free.
pub(crate) fn of(plugins: &Plugins) -> Vec<settings::shortcuts::Command> {
    settings::shortcuts::with_plugins(plugins.commands().into_iter().map(|command| {
        settings::shortcuts::Command {
            id: command.id.into(),
            label: command.title.into(),
            category: settings::shortcuts::Category::Plugins,
            key: command.key.map(Into::into),
            repeats: false,
            description: None,
        }
    }))
}
