module XGBoostTreeNodeEvaluator (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_fvalue,
    input logic in_is_missing,
    input logic in_has_missing,
    input logic in_has_categorical,
    input logic in_node_is_categorical,
    input logic [31:0] in_split_cond,
    input logic [31:0] in_left_child,
    input logic [31:0] in_default_child,
    input logic [3:0] in_cat_begin,
    input logic [3:0] in_cat_size,
    input logic [255:0] in_cat_words,
    output logic out_valid,
    input logic out_ready,
    output logic out_decision,
    output logic [31:0] out_next_node,
    output logic out_error
);
    logic decision, error;
    logic [31:0] next_node, magnitude, key;
    logic [7:0] exponent;
    integer position;

    function automatic logic is_nan(input logic [31:0] a);
        is_nan=(a[30:23]==8'hff && a[22:0]!=0);
    endfunction
    function automatic logic fp_less(input logic [31:0] a,b);
        if(is_nan(a)||is_nan(b)||(a[30:0]==0 && b[30:0]==0))fp_less=0;
        else if(a[31]!=b[31])fp_less=a[31];
        else if(a[31])fp_less=a[30:0]>b[30:0];
        else fp_less=a[30:0]<b[30:0];
    endfunction

    always_comb begin
        decision=0; error=0; next_node=in_default_child;
        magnitude={1'b0,in_fvalue[30:0]}; key=0; exponent=in_fvalue[30:23]; position=0;
        if(!(in_has_missing && in_is_missing))begin
            if(in_has_categorical && in_node_is_categorical)begin
                if(({1'b0,in_cat_begin}+{1'b0,in_cat_size})>5'd8 || is_nan(in_fvalue))error=1;
                else begin
                    decision=1;
                    if(!(in_fvalue[31] && magnitude!=0) && magnitude<32'h4b800000)begin
                        if(exponent>=127)key={8'd0,1'b1,in_fvalue[22:0]}>>(150-exponent);
                        if(key<({28'd0,in_cat_size}<<5))begin
                            position=32*(in_cat_begin+(key>>5))+31-(key&31);
                            decision=!in_cat_words[position];
                        end
                    end
                end
            end else decision=fp_less(in_fvalue,in_split_cond);
            next_node=decision ? in_left_child : in_left_child+32'd1;
        end
        if(error)begin decision=0;next_node=0;end
    end

    assign in_ready=!rst && (!out_valid || out_ready);
    always_ff @(posedge clk)begin
        if(rst)begin
            out_valid<=0;out_decision<=0;out_next_node<=0;out_error<=0;
        end else if(in_ready)begin
            out_valid<=in_valid;
            if(in_valid)begin
                out_decision<=decision;out_next_node<=next_node;out_error<=error;
            end
        end
    end
endmodule
