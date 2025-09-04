((python-mode
  . ((eval . (ruff-format-on-save-mode))
     (fill-column . 88)
     (python-indent-offset . 4)
     (init/inhibit-buffer-formatting t)
     (eglot-workspace-configuration
      . ((:pylsp . (:plugins (:pycodestyle (:enabled :json-false)
                                           :autopep8 (:enabled :json-false)
                                           :flake8 (:enabled :json-false)
                                           )))))))

 (python-ts-mode
  . ((eval . (ruff-format-on-save-mode))
     (fill-column . 88)
     (python-indent-offset . 4)
     (init/inhibit-buffer-formatting t)
     (eglot-workspace-configuration
      . ((:pylsp . (:plugins (:pycodestyle (:enabled :json-false)
                                           :autopep8 (:enabled :json-false)
                                           :flake8 (:enabled :json-false)
                                           ))))))))
