//! The commands the shortcuts follow: the built-ins and those of the plugins that loaded.

use crate::plugins::Plugins;
use api_types::handlers::CommandInfo;
use settings::shortcuts::{COMMANDS, Category, Command, with_plugins};

fn command(info: &CommandInfo) -> Command {
    Command {
        id: info.id.clone().into(),
        label: info.title.clone().into(),
        category: Category::Plugins,
        key: info.key.clone().map(Into::into),
        repeats: false,
        description: None,
    }
}

/// The built-in commands and those of the plugins that loaded, with each plugin default key that is free.
pub(crate) fn of(plugins: &Plugins) -> Vec<Command> {
    with_plugins(plugins.commands().iter().map(command))
}

/// Sets the `key` of each of `commands`, which are in plugin order, to the default it ships with: its own when
/// [`with_plugins`] keeps it, else none. The listing carries this, so a dialog that resets a command to its default
/// agrees with the keys the app binds.
pub(crate) fn settle_default_keys(mut commands: Vec<&mut CommandInfo>) {
    let settled = with_plugins(commands.iter().map(|info| command(info)));
    for (info, settled) in commands
        .iter_mut()
        .zip(settled.into_iter().skip(COMMANDS.len()))
    {
        info.key = settled.key.map(Into::into);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_default_the_app_or_an_earlier_plugin_holds_is_listed_unbound() {
        let info = |id: &str, key: &str| CommandInfo {
            id: id.to_owned(),
            title: id.to_owned(),
            on: handler_api::On::App,
            key: Some(key.to_owned()),
            runs: "view".to_owned(),
        };
        let mut commands = [
            info("p.free", "ctrl+alt+e"),
            info("p.taken", "Ctrl+S"),
            info("q.again", "Ctrl+Alt+E"),
        ];
        settle_default_keys(commands.iter_mut().collect());
        let keys: Vec<_> = commands
            .iter()
            .map(|command| command.key.as_deref())
            .collect();
        assert_eq!(keys, [Some("Ctrl+Alt+E"), None, None]);
    }
}
