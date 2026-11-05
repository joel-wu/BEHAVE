module dynamic_equalizer #(
    parameter int TAP_NUM=7,
    parameter int DATA_WIDTH=16,
    parameter int COEFF_WIDTH=16,
    parameter int MU=15
) (
    input logic clk, rst_n,
    input logic signed [DATA_WIDTH-1:0] data_in_real, data_in_imag,
    input logic signed [DATA_WIDTH-1:0] desired_real, desired_imag,
    output logic signed [DATA_WIDTH-1:0] data_out_real, data_out_imag
);
    logic signed [15:0] xr[0:TAP_NUM-1], xi[0:TAP_NUM-1];
    logic signed [15:0] wr[0:TAP_NUM-1], wi[0:TAP_NUM-1];
    logic signed [15:0] nxr[0:TAP_NUM-1], nxi[0:TAP_NUM-1];
    logic signed [15:0] nwr[0:TAP_NUM-1], nwi[0:TAP_NUM-1];
    logic signed [15:0] yr, yi, er, ei;
    logic signed [63:0] sr, si;
    function automatic signed [63:0] wide(input signed [15:0] v);
        wide={{48{v[15]}},v};
    endfunction
    function automatic signed [63:0] mul(input signed [15:0] a,b);
        mul=wide(a)*wide(b);
    endfunction
    function automatic signed [15:0] clip(input signed [63:0] v);
        if (v>64'sd32767) clip=16'sh7fff;
        else if (v < -64'sd32768) clip=16'sh8000;
        else clip=v[15:0];
    endfunction
    always_comb begin
        nxr[0]=data_in_real; nxi[0]=data_in_imag;
        for(int k=1;k<TAP_NUM;k++) begin nxr[k]=xr[k-1]; nxi[k]=xi[k-1]; end
        sr=0; si=0;
        for(int k=0;k<TAP_NUM;k++) begin
            sr=sr+((mul(wr[k],nxr[k])-mul(wi[k],nxi[k])) >>> 13);
            si=si+((mul(wr[k],nxi[k])+mul(wi[k],nxr[k])) >>> 13);
        end
        yr=clip(sr); yi=clip(si);
        er=clip(wide(desired_real)-wide(yr));
        ei=clip(wide(desired_imag)-wide(yi));
        for(int k=0;k<TAP_NUM;k++) begin
            nwr[k]=clip(wide(wr[k])+((mul(er,nxr[k])+mul(ei,nxi[k])) >>> (13+MU)));
            nwi[k]=clip(wide(wi[k])+((mul(ei,nxr[k])-mul(er,nxi[k])) >>> (13+MU)));
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            data_out_real<=0; data_out_imag<=0;
            for(int k=0;k<TAP_NUM;k++) begin
                xr[k]<=0; xi[k]<=0; wi[k]<=0; wr[k]<=k==TAP_NUM/2 ? 16'sd8192 : 16'sd0;
            end
        end else begin
            data_out_real<=yr; data_out_imag<=yi;
            for(int k=0;k<TAP_NUM;k++) begin
                xr[k]<=nxr[k]; xi[k]<=nxi[k]; wr[k]<=nwr[k]; wi[k]<=nwi[k];
            end
        end
    end
endmodule
