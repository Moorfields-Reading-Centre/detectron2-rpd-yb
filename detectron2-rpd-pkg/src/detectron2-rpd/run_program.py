import pdb
import datasets.data as data
import pickle
from detectron2.config import get_cfg
from analysis_lib import grab_dataset
from detectron2.data import DatasetCatalog,MetadataCatalog
from detectron2.modeling import build_model
from detectron2.data import build_detection_test_loader
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.evaluation import inference_on_dataset, COCOEvaluator
from Ensembler import Ensembler
from analysis_lib import EvaluateClass,CreatePlotsRPD,OutputVis
import pandas as pd
import logging
import configargparse
import progressbar
import urllib
logging.basicConfig(level=logging.INFO)

import json
import os
import sys
from table_styles import styles
import urllib.request
import zipfile

os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

dpi= 120
class MyProgressBar():
    # https://stackoverflow.com/a/53643011/3826929
    # George C
    def __init__(self):
        self.pbar = None

    def __call__(self, block_num, block_size, total_size):
        if not self.pbar:
            self.pbar=progressbar.ProgressBar(maxval=total_size)
            self.pbar.start()

        downloaded = block_num * block_size
        if downloaded < total_size:
            self.pbar.update(downloaded)
        else:
            self.pbar.finish()

def create_dataset(dataset_name, extracted_path): # Creates dataset and pk file from extracted images.
    stored_data = data.rpd_data(extracted_path)
    pickle.dump(stored_data, open(os.path.join(data.script_dir,f"{dataset_name}.pk"), "wb"))
    
def configure_model():
    cfg = get_cfg()
    moddir = os.path.dirname(os.path.realpath(__file__))
    name = 'working.yaml'
    cfg_path = os.path.join(moddir, name)
    cfg.merge_from_file(cfg_path)
    return cfg

def register_dataset(dataset_name):
    for name in [dataset_name]:
        try:
            DatasetCatalog.register(name, grab_dataset(name))
        except:
            print('Already registered.')
        MetadataCatalog.get(name).thing_classes = ["rpd"]

def run_prediction(cfg, dataset_name, output_path):
    print("start function run_prediction")
    print("cfg: ", cfg)
    pdb.set_trace()
    model = build_model(cfg)  # returns a torch.nn.Module
    print("model")
    myloader = build_detection_test_loader(cfg,dataset_name) 
    print("myloader")
    myeval = COCOEvaluator(dataset_name,tasks={'bbox','segm'},output_dir =output_path) #produces _coco_format.json when initialized
    print("myeval")
    for mdl in ("fold1", "fold2", "fold3", "fold4","fold5"):
        print(f"mdl: {mdl}")
        extract_directory = 'Models'
        if not os.path.isdir(extract_directory):
            print("Models directory does not exist! Making models directory...")
            os.mkdir(extract_directory)
            url = 'https://s3.us-west-2.amazonaws.com/comp.ophthalmology.uw.edu/models.zip'
            path_to_zip_file, headers = urllib.request.urlretrieve(url, reporthook = MyProgressBar())
            with zipfile.ZipFile(path_to_zip_file, 'r') as zip_ref:
                zip_ref.extractall(extract_directory)
        print("Loading model weights...")
        file_name = mdl + "_model_final.pth"
        print(file_name)
        model_weights_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), extract_directory, file_name)
        print(model_weights_path)
        DetectionCheckpointer(model).load(model_weights_path) # load a file, usually from cfg.MODEL.WEIGHTS
        model.eval() #set model in evaluation mode
        myeval.reset()
        output_dir = os.path.join(output_path, mdl)
        myeval._output_dir = output_dir
        print("Running inference with model ", mdl)
        results_i = inference_on_dataset(model, myloader, myeval) #produces coco_instance_results.json when myeval.evaluate is called
    print("Done with predictions!")

def run_ensemble(dataset_name, output_path, iou_thresh = 0.2):
    ens = Ensembler(output_path,dataset_name,["fold1", "fold2", "fold3", "fold4","fold5"],  iou_thresh=iou_thresh)
    ens.mean_score_nms()
    ens.save_coco_instances()
    return ens

def evaluate_dataset(dataset_name, output_path,iou_thresh = 0.2,prob_thresh = 0.5):
    myeval = EvaluateClass(dataset_name, output_path, iou_thresh = iou_thresh, prob_thresh=prob_thresh,evalsuper=False)
    myeval.evaluate()
    with open(os.path.join(output_path,'scalar_dict.json'),"w") as outfile:
        json.dump(obj=myeval.summarize_scalars(),fp=outfile)
    return myeval

def create_table(myeval):
    dataset_table = CreatePlotsRPD.initfromcoco(myeval.mycoco,myeval.prob_thresh)
    dataset_table.dfimg.sort_index(inplace=True)
    return dataset_table
    #dataset_table.dfimg['scan'] = dataset_table.dfimg['scan'].astype('int') #depends on what we want scan field to be

def output_vol_predictions(dataset_table,vis,volID,output_path,output_mode='pred_overlay'):
    dfimg = dataset_table.dfimg
    imgids = dfimg[dfimg.volID ==volID].sort_index().index.values
    outname = os.path.join(output_path,f'{volID}.tiff')
    if output_mode=='pred_overlay':
        vis.output_pred_to_tiff(imgids,outname,pred_only=False)
    elif output_mode == 'pred_only':
        vis.output_pred_to_tiff(imgids,outname,pred_only=True)
    elif output_mode == 'originals':
        vis.output_ori_to_tiff(imgids,outname)
    elif output_mode == 'all':
        vis.output_all_to_tiff(imgids,outname)
    else:
        print(f'Invalid mode {output_mode} for function output_vol_predictions.')

def output_dataset_predictions(dataset_table,vis,output_path,output_mode = 'pred_overlay',draw_mode='default'):
    vis.set_draw_mode(draw_mode)
    os.makedirs(output_path,exist_ok=True) ## should this be set to exist_ok=False?
    for volID in dataset_table.dfvol.index:
        output_vol_predictions(dataset_table,vis,volID,output_path,output_mode)

def create_dfvol(dataset_name, output_path, dataset_table):
    dfvol = dataset_table.dfvol.sort_values(by=['dt_instances'],ascending=False)
    with pd.option_context('styler.render.max_elements', dfvol.size+1):
        html_str = dfvol.style.format('{:.0f}').set_table_styles(styles).to_html()
    html_file = open(os.path.join(output_path, 'dfvol_'+dataset_name+'.html'),'w')
    html_file.write(html_str)
    html_file.close()

def create_dfimg(dataset_name, output_path, dataset_table):
    dfimg = dataset_table.dfimg.sort_index()
    with pd.option_context('styler.render.max_elements', dfimg.size+1):
        html_str = dfimg.style.set_table_styles(styles).to_html()
    html_file = open(os.path.join(output_path, 'dfimg_'+dataset_name+'.html'),'w')
    html_file.write(html_str)
    html_file.close()

def main(args):
    dataset_name = None
    input_dir = None
    extracted = None
    output = None
    run_ext = True
    run_inf = True
    make_table = True
    make_visuals = False
    bm = False
    bmo = False
    imo = False
    parser = configargparse.ArgParser(description='Run the detectron2 pipeline.')
    parser.add('--config', required = True, is_config_file = True, help = 'The path to your config file.')
    parser.add('--dataset_name', metavar = 'N', type=str, help='The name of your dataset.')
    parser.add('--input_dir', metavar = 'I', type=str, help='The path to the directory containing your vol/dicom files.'  )
    parser.add('--extracted_dir', metavar = 'E', type=str, help='The path to the directory where extracted images will be stored.')
    parser.add('--input_format', type=str, help='Format of the files to extract: VOL or DICOM.')
    parser.add('--output_dir', metavar = 'O', type=str, help='The path to the directory where model predictions and other data will be stored.')
    parser.add('--run_extract', action ='store_true', help='Extract images from your input files (.vol/.dicom).')
    parser.add('--run_inference', action ='store_true', help='Run inference on extracted images. Note: Files must already be extracted!')
    parser.add('--create_tables', action ='store_true', help='Create dataset html of model outputs. Note: Inference must already be done!')
    parser.add('--create_visuals', action ='store_true', help='Create visualizations of model outputs. Note: Inference must already be done and bm/bmo/im flags set!')
    parser.add('--binary_mask', action ='store_true', help='Output binary mask tif files. Note: create_visuals flag must be included!')
    parser.add('--binary_mask_overlay', action ='store_true', help='Output binary mask overlay tif files. Note: create_visuals flag must be included!')
    parser.add('--instance_mask_overlay', action ='store_true', help='Output instance mask overlay tif files. Note: create_visuals flag must be included!')
    args = parser.parse_args()
    print(args)
    dataset_name = args.dataset_name
    input_dir = args.input_dir
    extracted = args.extracted_dir
    input_format = args.input_format
    output = args.output_dir
    run_ext = args.run_extract
    run_inf = args.run_inference
    make_table = args.create_tables
    make_visuals = args.create_visuals
    bm = args.binary_mask
    bmo = args.binary_mask_overlay
    imo = args.instance_mask_overlay
    iou_thresh = 0.2
    prob_thresh = 0.5
    if run_ext:
        if not os.path.isdir(extracted):
            print("Extracted dir does not exist! Making extracted dir...")
            os.mkdir(extracted)
        data.extract_files(input_dir, extracted, input_format)
        print("Image extraction complete!")
    if run_inf:
        print("Creating dataset from extracted images...")
        create_dataset(dataset_name, extracted)
        print("Configuring model...")
        cfg = configure_model()
        print("Registering dataset...")
        register_dataset(dataset_name)
        if not os.path.isdir(output):
            print("Output dir does not exist! Making output dir...")
            os.mkdir(output)
        print("Running inference...")
        run_prediction(cfg, dataset_name, output)
        print("Inference complete, running ensemble...")
        run_ensemble(dataset_name, output)
        print("Ensemble complete!")
    if make_table or make_visuals:
        print("Registering dataset...")
        register_dataset(dataset_name)
        print("Evaluating dataset...")
        eval = evaluate_dataset(dataset_name, output, iou_thresh, prob_thresh)
        print("Creating dataset table...")
        table = create_table(eval)
        if make_table:
                create_dfvol(dataset_name, output, table)
                create_dfimg(dataset_name, output, table)
                print("Dataset htmls complete!")
        if make_visuals:
            register_dataset(dataset_name)
            vis = OutputVis(dataset_name,
                prob_thresh = eval.prob_thresh, 
                pred_mode = 'file',
                pred_file = os.path.join(output, 'coco_instances_results.json'),
                has_annotations=False)
            vis.scale=1.0
            if bm:
                print("Creating binary masks tif (no overlay)...")
                vis.annotation_color='w'
                output_dataset_predictions(table,vis,os.path.join(output,'predicted_binary_masks'),'pred_only','bw')
            if bmo:
                print("Creating binary masks tif (with overlay)...")
                output_dataset_predictions(table,vis,os.path.join(output,'predicted_binary_overlays'),'pred_overlay','bw')
            if imo:
                print("Creating instances masks tif (with overlay)...")
                output_dataset_predictions(table,vis,os.path.join(output,'predicted_instance_overlays'),'pred_overlay','default')
            print("Visualizations complete!")

if __name__ == "__main__":
    main(sys.argv[1:])
    # main_alt()